"""The runtime that enforces the registry. Collectors get a raw-writing scope, analysts get rows,
the gate gets rules and an evidence resolver. Nothing gets more than it declared."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from analysts.base import AnalystContext, AnalystInput
from collectors.base import CollectorResult
from collectors.registry import collect as _collect
from config.settings import get_settings
from contracts.project import Project
from db.connection import ProjectScope, project_scope
from gate.evidence import load_project_rules, make_resolver
from gate.stage1_rules import Stage1Result, run_stage1
from llm.client import LLMClient
from orchestrator import registry
from orchestrator.runs import log_agent

# how the runtime reads each table an analyst declares
_RUN_SCOPED = {"raw_crawl_pages", "raw_vitals", "raw_sitemap_urls", "raw_serp", "raw_fetched_documents", "raw_search_status",
               "collection_gaps", "raw_keyword_metrics"}
_DATED = {"raw_gsc_performance": "date", "raw_ga4_daily": "date"}


class TransientError(RuntimeError):
    """Raised for failures a retry may fix (network, database connectivity). Nothing else is retried."""


@dataclass
class Runtime:
    db_url: str | None = None
    llm: LLMClient | None = None
    model: str | None = None
    collector_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)   # tests inject transports here
    analyst_overrides: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.model = self.model or get_settings().analyst_model

    def get_llm(self) -> LLMClient:
        if self.llm is None:
            from llm.client import build_client
            self.llm = build_client()
        return self.llm

    @contextmanager
    def scope(self, project_id: UUID, agent: str = "orchestrator") -> Iterator[ProjectScope]:
        spec = registry.spec(agent)
        with project_scope(project_id, caller=spec.caller, url=self.db_url, allowed_writes=registry.grants(agent)) as s:
            yield s

    def load_project(self, project_id: UUID) -> Project:
        with self.scope(project_id) as s:
            return Project.from_row(s.fetchone("select * from projects where id = %(project_id)s"))

    # ---- collectors ----
    def run_collector(self, project: Project, run_id: UUID, name: str, params: dict[str, Any] | None = None) -> CollectorResult:
        merged = {**(params or {}), **self.collector_overrides.get(name, {}), **self.collector_overrides.get("*", {})}
        res = _collect(name, project, run_id, merged, db_url=self.db_url)
        with self.scope(project.id) as s:
            log_agent(s, run_id, name, "collector", "partial" if res.partial else "ok",
                      {"rows_written": res.rows_written, "gaps": res.gaps, **{k: v for k, v in res.detail.items() if k != "exception"}})
        return res

    # ---- analysts ----
    def read_for(self, scope: ProjectScope, agent: str, run_id: UUID, params: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        rows: dict[str, list[dict[str, Any]]] = {}
        for table in registry.spec(agent).reads:
            if table in _RUN_SCOPED:
                run_ids = [run_id, *params.get("include_run_ids", [])]
                order = {"collection_gaps": "created_at", "raw_fetched_documents": "fetched_at"}.get(table, "collected_at")
                rows[table] = scope.fetchall(f"select * from {table} where project_id = %(project_id)s and run_id = any(%(run_ids)s) order by {order}, id",
                                             {"run_ids": run_ids})
            elif table in _DATED:
                # analysts compare against the prior period, so the read window is twice the period
                since, until = params.get("since"), params.get("until")
                if since and until:
                    from datetime import date as _date
                    from datetime import timedelta as _td
                    a, b = _date.fromisoformat(since), _date.fromisoformat(until)
                    since = (a - _td(days=(b - a).days + 1)).isoformat()
                rows[table] = scope.fetchall(
                    f"select * from {table} where project_id = %(project_id)s and (%(since)s::date is null or {_DATED[table]} >= %(since)s::date) "
                    f"and (%(until)s::date is null or {_DATED[table]} <= %(until)s::date) order by {_DATED[table]}",
                    {"since": since, "until": until})
            elif table == "runs":
                rows[table] = scope.fetchall("select id, workflow, status, started_at, ended_at, cost_usd from runs where project_id = %(project_id)s order by started_at desc limit 50")
            elif table in ("brand_rules", "critical_rules"):
                rows[table] = scope.fetchall(f"select * from {table} where project_id = %(project_id)s and active")
            elif table == "issues":
                rows[table] = scope.fetchall("select * from issues where project_id = %(project_id)s and status = 'open' order by created_at desc limit 500")
            else:
                rows[table] = scope.fetchall(f"select * from {table} where project_id = %(project_id)s")
        return rows

    def run_analyst(self, project: Project, run_id: UUID, name: str, params: dict[str, Any] | None = None) -> BaseModel:
        from analysts.registry import ANALYSTS

        params = {**(params or {}), **self.analyst_overrides.get(name, {})}
        spec = registry.spec(name)
        assert spec.kind == "analyst"
        with self.scope(project.id) as s:
            rows = self.read_for(s, name, run_id, params)
        digest = hashlib.sha256(json.dumps(rows, default=str, sort_keys=True).encode()).hexdigest()[:16]
        ctx = AnalystContext(llm=self.get_llm(), model=self.model)
        inp = AnalystInput(project=project, run_id=run_id, rows=rows, params=params)
        try:
            artifact = ANALYSTS[name](ctx, inp)
        except Exception as e:
            with self.scope(project.id) as s:
                log_agent(s, run_id, name, "analyst", "failed", {"error": f"{type(e).__name__}: {e}"}, digest, ctx.model,
                          ctx.tokens_in, ctx.tokens_out, ctx.cost_usd, retries=sum(u.retries for u in ctx.usage))
            raise
        with self.scope(project.id) as s:
            log_agent(s, run_id, name, "analyst", "ok", {"summary": _summary(artifact)}, digest, ctx.model,
                      ctx.tokens_in, ctx.tokens_out, ctx.cost_usd, retries=sum(u.retries for u in ctx.usage))
        return artifact

    # ---- gate ----
    def run_gate_stage1(self, project: Project, run_id: UUID, agent: str, artifact: BaseModel | dict[str, Any],
                        artifact_ref: UUID | None = None) -> Stage1Result:
        with self.scope(project.id, "gate_stage1") as s:
            rules = load_project_rules(s)
            result = run_stage1(agent, artifact, rules, make_resolver(s))
            s.insert("gate_results", {
                "run_id": run_id, "source_agent": agent, "artifact_ref": artifact_ref,
                "stage1_violations": [v.as_dict() for v in result.violations],
                "stage2_verdict": "blocked" if result.blocked else None,
                "detail": {"dropped": result.dropped, "blocked": result.blocked, "stage": 1},
            })
            log_agent(s, run_id, "gate_stage1", "gate", "blocked" if result.blocked else "ok",
                      {"violations": len(result.violations), "dropped": result.dropped})
        return result


    def run_gate_stage2(self, project: Project, run_id: UUID, agent: str, artifact: dict[str, Any],
                        artifact_ref: UUID | None = None):
        from gate.stage2_verify import load_documents, run_stage2

        with self.scope(project.id, "gate_stage2") as s:
            refs = {UUID(str(src["fetched_doc_id"])) for src in artifact.get("sources", []) if src.get("fetched_doc_id")}
            docs = load_documents(s, refs)
            verifier = get_settings().verifier_model
            result = run_stage2(artifact, docs, self.get_llm() if refs else None, model=verifier)
            s.insert("gate_results", {
                "run_id": run_id, "source_agent": agent, "artifact_ref": artifact_ref, "stage1_violations": [],
                "stage2_verdict": result.verdict, "claims_verified": result.claims_verified, "claims_cut": result.claims_cut,
                "detail": {"stage": 2, "claims": [c.__dict__ for c in result.claims], "findings": result.findings},
            })
            log_agent(s, run_id, "gate_stage2", "gate", result.verdict, {"verified": result.claims_verified, "cut": result.claims_cut, "findings": len(result.findings)},
                      model=verifier if result.model_called else None)
            if result.findings:
                s.audit("gate:gate_stage2", "stage2_findings", {"findings": result.findings}, run_id)
        return result


def _summary(artifact: BaseModel) -> dict[str, Any]:
    data = artifact.model_dump(mode="json")
    return {k: (len(v) if isinstance(v, list) else v) for k, v in data.items() if not isinstance(v, (dict, str)) or k == "agent"}
