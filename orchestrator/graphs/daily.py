"""Daily workflows: daily_probe (header_probe on critical paths) and daily_collect (gsc_performance)."""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from langgraph.graph import END, StateGraph

from orchestrator import approvals, notify
from orchestrator.graphs.state import RunState
from orchestrator.runtime import Runtime


def build_daily_probe(rt: Runtime) -> StateGraph:
    def probe(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        res = rt.run_collector(project, run_id, "header_probe", {"paths": list(project.critical_paths)})
        violations = res.detail.get("violations", [])
        if violations:
            with rt.scope(project.id) as s:
                notify.page_owner(s, run_id, violations)
                approvals.queue_approval(s, run_id, "page_owner", {"violations": violations},
                                         f"{len(violations)} protected route(s) are reachable by crawlers.", "collector:header_probe",
                                         {"kind": "acknowledge"}, severity="critical")
        return {"collectors": {"header_probe": {"rows_written": res.rows_written, "gaps": res.gaps}}, "critical_violations": violations, "status": "done"}

    g = StateGraph(RunState)
    g.add_node("probe", probe)
    g.set_entry_point("probe")
    g.add_edge("probe", END)
    return g


def build_daily_collect(rt: Runtime) -> StateGraph:
    def collect(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        day = (date.today() - timedelta(days=3)).isoformat()
        res = rt.run_collector(project, run_id, "gsc_performance", {"start_date": day, "end_date": day})
        return {"collectors": {"gsc_performance": {"rows_written": res.rows_written, "gaps": res.gaps}}, "status": "done"}

    g = StateGraph(RunState)
    g.add_node("collect", collect)
    g.set_entry_point("collect")
    g.add_edge("collect", END)
    return g
