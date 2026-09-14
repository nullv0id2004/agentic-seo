"""content_pipeline: doc_fetch -> content_analyst -> gate (stage 1 then 2) -> approval (publish_content).

Manual or calendar trigger. params: keyword, source_urls, notes. The monthly cap is enforced in the
analyst in code; this graph never bypasses it.
"""
from __future__ import annotations

from uuid import UUID

from langgraph.graph import END, StateGraph

from orchestrator import approvals
from orchestrator.gate_runner import analyse_and_gate
from orchestrator.graphs.state import RunState
from orchestrator.runtime import Runtime

WORKFLOW = "content_pipeline"


def build(rt: Runtime) -> StateGraph:
    def fetch(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        urls = state.get("params", {}).get("source_urls", [])
        res = rt.run_collector(project, run_id, "doc_fetch", {"urls": urls})
        return {"collectors": {**state.get("collectors", {}), "doc_fetch": {"rows_written": res.rows_written, "gaps": res.gaps, "partial": res.partial}}}

    def draft(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        params = state.get("params", {})
        with rt.scope(project.id) as s:
            crawl_runs = s.fetchall("select id from runs where project_id = %(project_id)s and workflow in ('monthly_full','post_deploy_audit') and status in ('done','paused_for_approval') order by started_at desc limit 1")
        res = analyse_and_gate(rt, project, run_id, "content", {"keyword": params.get("keyword"), "notes": params.get("notes", ""),
                                                                 "include_run_ids": [str(r["id"]) for r in crawl_runs]})
        return {"gate": {**state.get("gate", {}), "content": {k: v for k, v in res.items() if k != "artifact"}},
                "artifacts": {**state.get("artifacts", {}), "content": res["artifact"]}, "issues": res["written"]}

    def queue(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        art = state.get("artifacts", {}).get("content")
        written = state.get("issues", [])
        if not art or not written:
            return {"status": "done"}
        with rt.scope(project.id) as s:
            aid = approvals.queue_approval(
                s, run_id, "publish_content", {"brief_id": written[0], "title": art["title"], "keyword": art["keyword"], "word_count": len(art["draft"].split())},
                f"Publish '{art['title']}' targeting '{art['keyword']}' ({len(art['sources'])} verified source claim(s)).", "analyst:content",
                {"kind": "unpublish", "brief_id": written[0], "published_url": None})
        return {"approvals": [*state.get("approvals", []), str(aid)], "status": "paused_for_approval"}

    g = StateGraph(RunState)
    g.add_node("fetch", fetch)
    g.add_node("draft", draft)
    g.add_node("queue", queue)
    g.set_entry_point("fetch")
    g.add_edge("fetch", "draft")
    g.add_edge("draft", "queue")
    g.add_edge("queue", END)
    return g
