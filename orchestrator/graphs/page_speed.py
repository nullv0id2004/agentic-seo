"""page_speed: the vitals collector for the project's most visited pages, on demand
(python worker.py run <slug> page_speed <key>). The monthly run and every post-deploy audit measure them too; this
is for checking a page without waiting. Collector only: issues are raised and closed by the runs that analyse.

Which pages: the homepage, then any pages the caller names first (a deploy's changed pages), then the pages with
the most Search Console clicks in the last 28 days, up to projects.vitals_page_limit. Protected routes are never
measured: a vitals issue carries its URL, and a protected path in an artifact blocks the gate. Search Console pages
must also be in the site's latest sitemap when one was read: Search Console keeps clicks for addresses that now
redirect (run 52d25e67 measured korum's /about-company, which 301s to worldhire.com, and raised lcp_slow on it).
"""
from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urljoin, urlsplit
from uuid import UUID

from langgraph.graph import END, StateGraph

from collectors.html import normalise_url
from contracts.project import Project
from db.connection import ProjectScope
from orchestrator.graphs.state import RunState
from orchestrator.runtime import Runtime
from rules.indexability import rule_matches

WORKFLOW = "page_speed"


def pick_urls(project: Project, top_pages: Iterable[str], protected: Iterable[str], limit: int,
              first: Iterable[str] = ()) -> list[str]:
    """The homepage, then `first`, then `top_pages`, on the project's own hosts, protected routes left out."""
    home = f"https://{project.primary_domain}/"
    hosts = {d.lower().removeprefix("www.") for d in project.domains}
    patterns = list(protected)
    out: list[str] = []
    for raw in [home, *first, *top_pages]:
        u = urljoin(home, str(raw).strip()).split("#", 1)[0]
        parts = urlsplit(u)
        if parts.scheme not in ("http", "https") or (parts.hostname or "").lower().removeprefix("www.") not in hosts:
            continue
        if any(rule_matches(p, u) for p in patterns) or u in out:
            continue
        out.append(u)
        if len(out) >= limit:
            break
    return out


def vitals_urls(scope: ProjectScope, project: Project, limit: int, first: Iterable[str] = ()) -> list[str]:
    protected = [r["url_pattern"] for r in scope.fetchall(
        """select url_pattern from critical_rules where project_id = %(project_id)s and active
              and assertion in ('must_noindex', 'must_not_appear_in_sitemap')""")]
    top = [r["page"] for r in scope.fetchall(
        """select page from raw_gsc_performance
            where project_id = %(project_id)s and source = 'gsc' and page is not null and date >= current_date - interval '28 days'
            group by page order by sum(clicks) desc nulls last, page limit 200""")]
    listed = {r["url"] for r in scope.fetchall(
        """select url from raw_sitemap_urls where project_id = %(project_id)s
              and run_id = (select run_id from raw_sitemap_urls where project_id = %(project_id)s order by collected_at desc limit 1)""")}
    if listed:
        top = [u for u in top if normalise_url(u) in listed]
    return pick_urls(project, top, protected, limit, first)


def build(rt: Runtime) -> StateGraph:
    def collect(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        with rt.scope(project.id) as s:
            urls = vitals_urls(s, project, project.vitals_page_limit)
        res = rt.run_collector(project, UUID(state["run_id"]), "vitals", {"urls": urls})
        return {"collectors": {"vitals": {"rows_written": res.rows_written, "gaps": res.gaps, "partial": res.partial, "urls": len(urls)}},
                "status": "done"}

    g = StateGraph(RunState)
    g.add_node("collect", collect)
    g.set_entry_point("collect")
    g.add_edge("collect", END)
    return g
