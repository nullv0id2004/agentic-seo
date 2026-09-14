"""Persist gated artifacts into derived tables, and run analyst+gate+persist as one step."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from db.connection import ProjectScope, jsonb
from orchestrator.runtime import Runtime


def write_issues(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> list[UUID]:
    ids: list[UUID] = []
    for issue in artifact.get("issues", []):
        ids.append(scope.insert("issues", {
            "run_id": run_id, "url": issue.get("url"), "issue_type": issue["issue_type"], "severity": issue["severity"],
            "evidence": issue["evidence"], "evidence_ref": issue.get("evidence_ref"),
            "recommended_fix": issue.get("recommended_fix"), "claude_code_prompt": issue.get("claude_code_prompt"),
        }))
    return ids


def write_trend_events(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> list[UUID]:
    return [scope.insert("trend_events", {
        "run_id": run_id, "kind": e["kind"], "name": e["name"], "source_url": e["source_url"], "observed_on": e["observed_on"],
        "detail": e.get("detail"), "evidence_ref": e.get("evidence_ref"),
    }) for e in artifact.get("events", [])]


def write_report(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> UUID:
    metrics = {m["name"]: {k: v for k, v in m.items() if k != "name"} for m in artifact.get("metrics", [])}
    metrics["cutoff_date"] = artifact.get("cutoff_date")
    return scope.insert("reports", {
        "run_id": run_id, "period": artifact["period"], "metrics": metrics, "commentary": artifact.get("commentary"),
        "caveats": jsonb(artifact.get("caveats", [])),
    })


PERSISTERS = {"technical": write_issues, "ecommerce": write_issues, "trend": write_trend_events, "report": write_report}


def analyse_and_gate(rt: Runtime, project, run_id: UUID, agent: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one analyst, gate its output, persist when clean. Returns a JSON-safe summary for graph state."""
    artifact = rt.run_analyst(project, run_id, agent, params)
    result = rt.run_gate_stage1(project, run_id, agent, artifact)
    out: dict[str, Any] = {"blocked": result.blocked, "violations": [v.as_dict() for v in result.violations],
                           "dropped": result.dropped, "artifact": None, "written": []}
    if result.blocked:
        return out
    out["artifact"] = result.artifact
    persist = PERSISTERS.get(agent)
    if persist:
        with rt.scope(project.id) as s:
            written = persist(s, run_id, result.artifact)
        out["written"] = [str(w) for w in written] if isinstance(written, list) else [str(written)]
    return out
