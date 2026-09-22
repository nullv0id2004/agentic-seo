"""Daily workflows: daily_probe (header_probe on critical paths) and daily_collect (gsc_performance)."""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from langgraph.graph import END, StateGraph

from orchestrator import critical
from orchestrator.graphs.state import RunState
from orchestrator.runtime import Runtime


def build_daily_probe(rt: Runtime) -> StateGraph:
    def probe(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        res = rt.run_collector(project, run_id, "header_probe", {"paths": list(project.critical_paths)})
        violations = res.detail.get("violations", [])
        with rt.scope(project.id) as s:
            for r in critical.indexed_protected_routes(s):
                violations.append({"rule_key": "indexed_protected_route", "assertion": "must_noindex", "url": r["url"], "detail": "URL Inspection reports this protected route as indexable"})
        critical.handle_violations(rt, project, run_id, violations, "collector:header_probe")
        critical.clear_halt_if_clean(rt, project, run_id, violations)
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
        # Search Console finalises a day about three days later; GA4 is complete after one.
        gsc_day = (date.today() - timedelta(days=3)).isoformat()
        ga4_day = (date.today() - timedelta(days=1)).isoformat()
        out = {}
        for name, day in (("gsc_performance", gsc_day), ("ga4", ga4_day)):
            res = rt.run_collector(project, run_id, name, {"start_date": day, "end_date": day})
            out[name] = {"rows_written": res.rows_written, "gaps": res.gaps}
        return {"collectors": out, "status": "done"}

    g = StateGraph(RunState)
    g.add_node("collect", collect)
    g.set_entry_point("collect")
    g.add_edge("collect", END)
    return g
