"""Two collector-only workflows.

daily_rank: the serp collector for a project's top tracked keywords, every day at 06:15 IST, for projects with
projects.rank_tracking_limit > 0. Keywords mapped to a page come first, then the highest search volume. The weekly
monitor still checks every tracked keyword on Mondays.

competitor_overview: the DataForSEO Labs domain overview for the project and its competitors, on demand
(python worker.py run <slug> competitor_overview <key>); the monthly run collects it too.
"""
from __future__ import annotations

from uuid import UUID

from langgraph.graph import END, StateGraph

from db.connection import ProjectScope
from orchestrator.graphs.state import RunState
from orchestrator.runtime import Runtime

DAILY_RANK = "daily_rank"
COMPETITOR_OVERVIEW = "competitor_overview"


def top_keywords(scope: ProjectScope, limit: int) -> list[str]:
    rows = scope.fetchall(
        """select k.keyword from keywords k
             left join raw_keyword_metrics m on m.project_id = k.project_id and m.id = k.latest_metric_id
            where k.project_id = %(project_id)s and not k.blocked_for_index
            order by (k.mapped_url is null), m.volume desc nulls last, k.keyword
            limit %(n)s""", {"n": limit})
    return [r["keyword"] for r in rows]


def build_daily_rank(rt: Runtime) -> StateGraph:
    def collect(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        with rt.scope(project.id) as s:
            queries = top_keywords(s, project.rank_tracking_limit) if project.rank_tracking_limit > 0 else []
        if not queries:   # an empty list would make the serp collector fall back to every tracked keyword
            return {"collectors": {"serp": {"rows_written": 0, "gaps": 0, "skipped": "no keywords to track"}}, "status": "done"}
        res = rt.run_collector(project, run_id, "serp", {"queries": queries})
        return {"collectors": {"serp": {"rows_written": res.rows_written, "gaps": res.gaps, "partial": res.partial}}, "status": "done"}

    g = StateGraph(RunState)
    g.add_node("collect", collect)
    g.set_entry_point("collect")
    g.add_edge("collect", END)
    return g


def build_competitor_overview(rt: Runtime) -> StateGraph:
    def collect(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        res = rt.run_collector(project, UUID(state["run_id"]), "competitor_overview", {})
        return {"collectors": {"competitor_overview": {"rows_written": res.rows_written, "gaps": res.gaps, "partial": res.partial}}, "status": "done"}

    g = StateGraph(RunState)
    g.add_node("collect", collect)
    g.set_entry_point("collect")
    g.add_edge("collect", END)
    return g
