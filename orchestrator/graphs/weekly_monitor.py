"""weekly_monitor: serp -> search_status -> trend_analyst -> gate -> report append. Cron Mon 06:00 IST."""
from __future__ import annotations

from uuid import UUID

from langgraph.graph import END, StateGraph

from orchestrator.gate_runner import analyse_and_gate
from orchestrator.graphs.state import RunState
from orchestrator.runtime import Runtime

WORKFLOW = "weekly_monitor"


def build(rt: Runtime) -> StateGraph:
    def collect(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        out = dict(state.get("collectors", {}))
        for name in ("serp", "search_status", "gsc_inspection"):
            params = {}
            if name == "gsc_inspection":
                # weekly proof that protected routes are not indexed; the collector enforces the 2000/day cap
                params = {"urls": [f"https://{project.primary_domain}{p if p.startswith('/') else '/' + p}" for p in project.critical_paths]}
            res = rt.run_collector(project, run_id, name, params)
            out[name] = {"rows_written": res.rows_written, "gaps": res.gaps, "partial": res.partial}
        from orchestrator import critical
        with rt.scope(project.id) as s:
            indexed = critical.indexed_protected_routes(s)
        violations = [{"rule_key": "indexed_protected_route", "assertion": "must_noindex", "url": r["url"], "detail": "URL Inspection reports this protected route as indexable"} for r in indexed]
        critical.handle_violations(rt, project, run_id, violations, "collector:gsc_inspection")
        return {"collectors": out, "critical_violations": violations}

    def trend(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        with rt.scope(project.id) as s:
            prev = s.fetchall("select id from runs where project_id = %(project_id)s and workflow in ('weekly_monitor','monthly_full') and status = 'done' and id <> %(run_id)s order by started_at desc limit 1", {"run_id": run_id})
        res = analyse_and_gate(rt, project, run_id, "trend", {"include_run_ids": [str(r["id"]) for r in prev]})
        return {"gate": {**state.get("gate", {}), "trend": {k: v for k, v in res.items() if k != "artifact"}},
                "artifacts": {**state.get("artifacts", {}), "trend": res["artifact"]}}

    def report_append(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        res = analyse_and_gate(rt, project, run_id, "report", {**state.get("params", {}), "weekly": True})
        return {"gate": {**state.get("gate", {}), "report": {k: v for k, v in res.items() if k != "artifact"}},
                "report_id": res["written"][0] if res["written"] else None, "status": "done"}

    g = StateGraph(RunState)
    g.add_node("collect", collect)
    g.add_node("trend", trend)
    g.add_node("report_append", report_append)
    g.set_entry_point("collect")
    g.add_edge("collect", "trend")
    g.add_edge("trend", "report_append")
    g.add_edge("report_append", END)
    return g
