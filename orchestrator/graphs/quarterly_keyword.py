"""quarterly_keyword: keyword_discovery -> keyword_metrics -> keyword_analyst -> gate -> approvals (keyword_mapping).
Keywords the analyst cannot map to an existing page are kept as unmapped opportunities for content."""
from __future__ import annotations

from uuid import UUID

from langgraph.graph import END, StateGraph

from contracts.project import Project
from db.connection import ProjectScope
from orchestrator import approvals
from orchestrator.gate_runner import analyse_and_gate
from orchestrator.graphs.state import RunState
from orchestrator.runtime import Runtime
from rules.domains import brand_label
from rules.keywords import brand_tokens, on_topic, topic_words

WORKFLOW = "quarterly_keyword"


GSC_QUERY_LIMIT = 200
DISCOVERED_LIMIT = 60      # discovered keywords added to the priced universe per run
OPPORTUNITY_LIMIT = 40     # unmapped keywords kept per run as content opportunities


def discovered_keywords(scope: ProjectScope, run_id: UUID, limit: int = DISCOVERED_LIMIT, extra_brands: list[str] | None = None) -> list[str]:
    """The highest-volume keywords keyword_discovery found in this run, minus brand searches: navigational
    intent (DataForSEO's own label: "amazon careers jobs", "apna jobs" in run be9ad355) and any keyword
    naming a competitor. Deterministic."""
    rows = scope.fetchall(
        """select keyword, max(search_volume) as volume, array_remove(array_agg(distinct competitor), null) as competitors,
                  bool_or(intent = 'navigational') as navigational
             from raw_keyword_ideas where project_id = %(project_id)s and run_id = %(run_id)s
            group by keyword order by max(search_volume) desc nulls last, keyword""", {"run_id": run_id})
    brands = {brand_label(c) for r in rows for c in (r["competitors"] or [])} | set(extra_brands or [])
    brands = {b for b in brands if len(b) > 2}
    out = [r["keyword"] for r in rows if not r["navigational"]
           and not any(b in r["keyword"].split() or b in r["keyword"].replace(" ", "") for b in brands)]
    return out[:limit]


def opportunities(project: Project, proposals: list[dict]) -> list[dict]:
    """Unmapped keywords worth keeping as content to write. Each needs the analyst's explicit relevance AND,
    unless it is a seed, a distinctive topic word from the seeds. Run b3245cad: the model called "government
    jobs odisha" relevant to a senior-hiring platform; the topic word is the deterministic backstop."""
    words = topic_words(project.keyword_seeds, brand_tokens(project.display_name or project.slug, list(project.domains),
                                                            project.brand_context_terms), project.vertical)
    seeds = {k.lower() for k in project.keyword_seeds}
    return [k for k in proposals if not k.get("mapped_url") and not k.get("blocked_for_index")
            and k.get("intent") != "navigational" and k.get("relevant") is True
            and (k["keyword"].lower() in seeds or on_topic(k["keyword"], words))]


def keyword_universe(scope: ProjectScope, project: Project, requested: list[str]) -> list[str]:
    """What the quarterly run prices: the caller's list, the project's configured seeds, and the queries
    Search Console has actually shown the site for in the last 90 days (most impressions first). The
    collector adds the keywords table on its own. Deterministic; no model involved."""
    rows = scope.fetchall(
        """select query from raw_gsc_performance
            where project_id = %(project_id)s and query is not null and date >= current_date - 90
            group by query order by sum(impressions) desc nulls last, query limit %(limit)s""", {"limit": GSC_QUERY_LIMIT})
    seen: dict[str, None] = {}
    for k in [*requested, *project.keyword_seeds, *(r["query"] for r in rows)]:
        k = (k or "").strip().lower()
        if k:
            seen.setdefault(k, None)
    return list(seen)


def build(rt: Runtime) -> StateGraph:
    def collect(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        out = dict(state.get("collectors", {}))
        res = rt.run_collector(project, run_id, "keyword_discovery", {})
        out["keyword_discovery"] = {"rows_written": res.rows_written, "gaps": res.gaps, "partial": res.partial}
        with rt.scope(project.id) as s:
            universe = keyword_universe(s, project, state.get("params", {}).get("keywords", []))
            universe += [k for k in discovered_keywords(s, run_id, extra_brands=[brand_label(c) for c in project.competitors]) if k not in universe]
        for name in ("keyword_metrics", "ai_keyword_metrics"):
            res = rt.run_collector(project, run_id, name, {"keywords": universe})
            out[name] = {"rows_written": res.rows_written, "gaps": res.gaps, "partial": res.partial}
        return {"collectors": out}

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
        proposals = [k for k in art.get("keywords", []) if k.get("mapped_url") and not k.get("blocked_for_index") and k.get("relevant") is not False]
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
        # Keywords no existing page fits are content opportunities: tracked unmapped, never mapped here.
        gaps = opportunities(project, art.get("keywords", []))
        gaps.sort(key=lambda k: -(k.get("volume") or k.get("volume_high") or 0))
        if gaps:
            with rt.scope(project.id) as s:
                for k in gaps[:OPPORTUNITY_LIMIT]:
                    s.execute("""insert into keywords (project_id, keyword, intent, cluster, updated_at)
                                 values (%(project_id)s, %(kw)s, %(intent)s, %(cluster)s, now())
                                 on conflict (project_id, keyword) do update set intent = coalesce(keywords.intent, excluded.intent),
                                   cluster = coalesce(keywords.cluster, excluded.cluster)""",
                              {"kw": k["keyword"], "intent": k.get("intent"), "cluster": k.get("cluster")})
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
