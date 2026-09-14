"""monthly_full: all collectors -> all enabled analysts -> gate -> report -> approvals digest. Cron 4th 06:00 IST.

Runs on the fourth because Search Console lags two to three days. The report states its cutoff.
"""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from langgraph.graph import END, StateGraph

from orchestrator import approvals, critical, notify
from orchestrator.gate_runner import analyse_and_gate
from orchestrator.graphs.state import RunState
from orchestrator.runtime import Runtime

WORKFLOW = "monthly_full"
COLLECTORS = ("gsc_performance", "ga4", "site_crawl", "gsc_inspection", "header_probe", "vitals", "serp", "search_status", "backlinks")
ANALYST_ORDER = ("technical", "ecommerce", "keyword", "onpage", "trend", "offpage", "report")


def build(rt: Runtime) -> StateGraph:
    def collect(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        params = state.get("params", {})
        end = date.fromisoformat(params["until"]) if params.get("until") else date.today() - timedelta(days=3)
        start = date.fromisoformat(params["since"]) if params.get("since") else end.replace(day=1)
        out = dict(state.get("collectors", {}))
        crit: list = []
        for name in COLLECTORS:
            cparams = {}
            if name in ("gsc_performance", "ga4"):
                cparams = {"start_date": start.isoformat(), "end_date": end.isoformat()}
            elif name == "header_probe":
                cparams = {"paths": list(project.critical_paths)}
            elif name == "site_crawl":
                cparams = {"max_pages": params.get("max_pages", 300)}
            res = rt.run_collector(project, run_id, name, cparams)
            out[name] = {"rows_written": res.rows_written, "gaps": res.gaps, "partial": res.partial}
            if name == "header_probe" and res.detail.get("violations"):
                crit = res.detail["violations"]
        critical.handle_violations(rt, project, run_id, crit, "collector:header_probe")
        critical.clear_halt_if_clean(rt, project, run_id, crit)
        return {"collectors": out, "critical_violations": crit, "params": {**params, "since": start.isoformat(), "until": end.isoformat()}}

    def analyse(state: RunState) -> RunState:
        from analysts.registry import ANALYSTS

        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        gate = dict(state.get("gate", {}))
        artifacts = dict(state.get("artifacts", {}))
        issues = list(state.get("issues", []))
        report_id = None
        with rt.scope(project.id) as s:
            prev = s.fetchall("select id from runs where project_id = %(project_id)s and workflow in ('weekly_monitor','monthly_full') and status = 'done' and id <> %(run_id)s order by started_at desc limit 1", {"run_id": run_id})
        for agent in ANALYST_ORDER:
            if agent not in project.enabled_agents or agent not in ANALYSTS:
                continue
            params = dict(state.get("params", {}))
            if agent == "trend":
                params["include_run_ids"] = [str(r["id"]) for r in prev]
            res = analyse_and_gate(rt, project, run_id, agent, params)
            gate[agent] = {k: v for k, v in res.items() if k != "artifact"}
            artifacts[agent] = res["artifact"]
            if agent in ("technical", "ecommerce"):
                issues.extend(res["written"])
            if agent == "report" and res["written"]:
                report_id = res["written"][0]
        return {"gate": gate, "artifacts": artifacts, "issues": issues, "report_id": report_id}

    def digest(state: RunState) -> RunState:
        project = rt.load_project(UUID(state["project_id"]))
        run_id = UUID(state["run_id"])
        queued = list(state.get("approvals", []))
        with rt.scope(project.id) as s:
            for agent in ("technical", "ecommerce"):
                art = state.get("artifacts", {}).get(agent) or {}
                for issue in art.get("issues", []):
                    if issue["severity"] in ("critical", "high") and issue.get("claude_code_prompt"):
                        aid = approvals.queue_approval(
                            s, run_id, "open_fix_pr",
                            {"issue_type": issue["issue_type"], "url": issue.get("url"), "claude_code_prompt": issue["claude_code_prompt"],
                             "recommended_fix": issue["recommended_fix"], "evidence_ref": issue.get("evidence_ref")},
                            f"Open a fix PR for {issue['issue_type']} ({issue['severity']}) on {issue.get('url') or 'a protected route'}: {issue['recommended_fix']}",
                            f"analyst:{agent}", {"kind": "close_pr_and_delete_branch", "pr_number": None, "branch": None},
                            severity="critical" if issue["severity"] == "critical" else "normal")
                        queued.append(str(aid))
            pitches = (state.get("artifacts", {}).get("offpage") or {}).get("pitches", [])
            pitch_ids = state.get("gate", {}).get("offpage", {}).get("written", [])
            for pid, p in zip(pitch_ids, pitches, strict=False):
                aid = approvals.queue_approval(
                    s, run_id, "send_pitch", {"pitch_id": pid, "outlet_url": p["outlet_url"], "subject": p["subject"], "body": p["body"]},
                    f"Send outreach to {p['outlet_url']}: {p['subject']}", "analyst:offpage",
                    {"kind": "send_retraction", "pitch_id": pid, "outlet_url": p["outlet_url"]})
                queued.append(str(aid))
            notify.daily_digest(s)
        return {"approvals": queued, "status": "paused_for_approval" if queued else "done"}

    g = StateGraph(RunState)
    g.add_node("collect", collect)
    g.add_node("analyse", analyse)
    g.add_node("digest", digest)
    g.set_entry_point("collect")
    g.add_edge("collect", "analyse")
    g.add_edge("analyse", "digest")
    g.add_edge("digest", END)
    return g
