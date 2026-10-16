"""link_plan: site_crawl -> backlink_gap -> linkbuilding analyst -> gate. Manual: python worker.py run <slug> link_plan.

The monthly run builds the link plan too. This refreshes it on demand, for instance after creating profiles or
adding them to sameAs, so the checklist ticks off without waiting a month. The crawl and the backlink rows are
collected in this run because the analyst reads only its own run's rows.
"""
from __future__ import annotations

from uuid import UUID

from langgraph.graph import END, StateGraph

from orchestrator.gate_runner import analyse_and_gate
from orchestrator.graphs.state import RunState
from orchestrator.runtime import Runtime

WORKFLOW = "link_plan"


def build(rt: Runtime) -> StateGraph:
    def collect(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        params = state.get("params", {})
        out = dict(state.get("collectors", {}))
        for name, cparams in (("site_crawl", {"max_pages": params.get("max_pages", 300)}), ("backlink_gap", {})):
            res = rt.run_collector(project, run_id, name, cparams)
            out[name] = {"rows_written": res.rows_written, "gaps": res.gaps, "partial": res.partial}
        return {"collectors": out}

    def analyse(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        if "linkbuilding" not in project.enabled_agents:
            return {"errors": [*state.get("errors", []), "linkbuilding is not in enabled_agents"], "status": "done"}
        res = analyse_and_gate(rt, project, UUID(state["run_id"]), "linkbuilding", dict(state.get("params", {})))
        return {"gate": {"linkbuilding": {k: v for k, v in res.items() if k != "artifact"}},
                "artifacts": {"linkbuilding": res["artifact"]}, "status": "done"}

    g = StateGraph(RunState)
    g.add_node("collect", collect)
    g.add_node("analyse", analyse)
    g.set_entry_point("collect")
    g.add_edge("collect", "analyse")
    g.add_edge("analyse", END)
    return g
