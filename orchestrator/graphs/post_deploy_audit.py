"""post_deploy_audit: header_probe -> site_crawl (delta) -> technical_analyst -> gate -> approvals.

Triggered by the Vercel deploy webhook. The header probe runs first and a critical rule failure pages
the owner before anything else happens.
"""
from __future__ import annotations

from uuid import UUID

from langgraph.graph import END, StateGraph

from orchestrator import approvals, critical
from orchestrator.gate_runner import issue_fingerprint, resolve_unseen_issues, write_issues
from orchestrator.graphs.state import RunState
from orchestrator.runtime import Runtime

WORKFLOW = "post_deploy_audit"


def build(rt: Runtime) -> StateGraph:
    def header_probe(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        params = state.get("params", {})
        paths = list(project.critical_paths) + [u for u in params.get("changed_urls", [])]
        res = rt.run_collector(project, run_id, "header_probe", {"paths": paths})
        violations = res.detail.get("violations", [])
        out: RunState = {"collectors": {**state.get("collectors", {}), "header_probe": _cr(res)}, "critical_violations": violations}
        aid = critical.handle_violations(rt, project, run_id, violations, "collector:header_probe")
        critical.clear_halt_if_clean(rt, project, run_id, violations)
        if aid:
            out["approvals"] = [*state.get("approvals", []), str(aid)]
        return out

    def site_crawl(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        params = state.get("params", {})
        cparams = {"urls": params["changed_urls"]} if params.get("changed_urls") else {"max_pages": params.get("max_pages", 150)}
        res = rt.run_collector(project, run_id, "site_crawl", cparams)
        return {"collectors": {**state.get("collectors", {}), "site_crawl": _cr(res)}}

    def technical_analyst(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        artifact = rt.run_analyst(project, UUID(state["run_id"]), "technical")
        return {"artifacts": {**state.get("artifacts", {}), "technical": artifact.model_dump(mode="json")}}

    def gate(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        result = rt.run_gate_stage1(project, run_id, "technical", state["artifacts"]["technical"])
        gate_state = {"blocked": result.blocked, "violations": [v.as_dict() for v in result.violations], "dropped": result.dropped}
        out: RunState = {"gate": {**state.get("gate", {}), "technical": gate_state}}
        if not result.blocked:
            with rt.scope(project.id) as s:
                ids = write_issues(s, run_id, result.artifact)
                resolve_unseen_issues(s, run_id, [result.artifact])
            out["issues"] = [str(i) for i in ids]
            out["artifacts"] = {**state.get("artifacts", {}), "technical": result.artifact}
        return out

    def queue_approvals(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        queued = list(state.get("approvals", []))
        if state.get("gate", {}).get("technical", {}).get("blocked"):
            return {"status": "done"}
        issues = state.get("artifacts", {}).get("technical", {}).get("issues", [])
        with rt.scope(project.id) as s:
            for issue_id, issue in zip(state.get("issues", []), issues, strict=False):
                if issue["severity"] in ("critical", "high") and issue.get("claude_code_prompt"):
                    fp = issue_fingerprint(issue)
                    if approvals.already_queued(s, "open_fix_pr", fp):
                        continue
                    aid = approvals.queue_approval(
                        s, run_id, "open_fix_pr",
                        {"issue_id": issue_id, "issue_fingerprint": fp, "issue_type": issue["issue_type"], "url": issue.get("url"),
                         "claude_code_prompt": issue["claude_code_prompt"], "recommended_fix": issue["recommended_fix"]},
                        f"Open a fix PR for {issue['issue_type']} ({issue['severity']}) on {issue.get('url') or 'a protected route'}: {issue['recommended_fix']}",
                        "analyst:technical", {"kind": "close_pr_and_delete_branch", "pr_number": None, "branch": None},
                        severity="critical" if issue["severity"] == "critical" else "normal")
                    queued.append(str(aid))
        return {"approvals": queued, "status": "paused_for_approval" if queued else "done"}

    g = StateGraph(RunState)
    g.add_node("header_probe", header_probe)
    g.add_node("site_crawl", site_crawl)
    g.add_node("technical_analyst", technical_analyst)
    g.add_node("gate", gate)
    g.add_node("approvals", queue_approvals)
    g.set_entry_point("header_probe")
    g.add_edge("header_probe", "site_crawl")
    g.add_edge("site_crawl", "technical_analyst")
    g.add_edge("technical_analyst", "gate")
    g.add_edge("gate", "approvals")
    g.add_edge("approvals", END)
    return g


def _cr(res) -> dict:
    return {"rows_written": res.rows_written, "gaps": res.gaps, "partial": res.partial,
            **{k: v for k, v in res.detail.items() if k != "exception"}}
