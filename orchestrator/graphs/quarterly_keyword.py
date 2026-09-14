"""quarterly_keyword: keyword_metrics -> keyword_analyst -> gate -> approvals (keyword_mapping)."""
from __future__ import annotations

from uuid import UUID

from langgraph.graph import END, StateGraph

from orchestrator import approvals
from orchestrator.gate_runner import analyse_and_gate
from orchestrator.graphs.state import RunState
from orchestrator.runtime import Runtime

WORKFLOW = "quarterly_keyword"


def build(rt: Runtime) -> StateGraph:
    def collect(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        res = rt.run_collector(project, run_id, "keyword_metrics", {"keywords": state.get("params", {}).get("keywords", [])})
        return {"collectors": {**state.get("collectors", {}), "keyword_metrics": {"rows_written": res.rows_written, "gaps": res.gaps, "partial": res.partial}}}

    def analyse(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        # keywords already claimed by cross-linked projects are read with their own scope and passed in as data
        reserved: list[str] = []
        with rt.scope(project.id) as s:
            crawl_runs = s.fetchall("select id from runs where project_id = %(project_id)s and workflow in ('monthly_full','post_deploy_audit') and status in ('done','paused_for_approval') and id <> %(run_id)s order by started_at desc limit 1", {"run_id": run_id})
        for other in state.get("params", {}).get("cross_link_exclusions", []):
            with rt.scope(UUID(other)) as s:
                reserved += [r["keyword"] for r in s.fetchall("select keyword from keywords where project_id = %(project_id)s and mapped_url is not null")]
        res = analyse_and_gate(rt, project, run_id, "keyword", {"reserved_keywords": reserved, "include_run_ids": [str(r["id"]) for r in crawl_runs]})
        return {"gate": {**state.get("gate", {}), "keyword": {k: v for k, v in res.items() if k != "artifact"}},
                "artifacts": {**state.get("artifacts", {}), "keyword": res["artifact"]}}

    def queue(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        art = state.get("artifacts", {}).get("keyword") or {}
        queued = list(state.get("approvals", []))
        proposals = [k for k in art.get("keywords", []) if k.get("mapped_url") and not k.get("blocked_for_index")]
        if proposals:
            with rt.scope(project.id) as s:
                current = {r["keyword"]: r["mapped_url"] for r in s.fetchall("select keyword, mapped_url from keywords where project_id = %(project_id)s")}
                changes = [p for p in proposals if current.get(p["keyword"]) != p["mapped_url"]]
                if changes:
                    aid = approvals.queue_approval(
                        s, run_id, "keyword_mapping", {"mappings": [{"keyword": p["keyword"], "mapped_url": p["mapped_url"], "intent": p.get("intent")} for p in changes]},
                        f"Map {len(changes)} keyword(s) to pages (proposed by the keyword analyst).", "analyst:keyword",
                        {"kind": "restore_previous_mapping", "previous": [{"keyword": p["keyword"], "mapped_url": current.get(p["keyword"])} for p in changes]})
                    queued.append(str(aid))
        return {"approvals": queued, "status": "paused_for_approval" if queued else "done"}

    g = StateGraph(RunState)
    g.add_node("collect", collect)
    g.add_node("analyse", analyse)
    g.add_node("queue", queue)
    g.set_entry_point("collect")
    g.add_edge("collect", "analyse")
    g.add_edge("analyse", "queue")
    g.add_edge("queue", END)
    return g
