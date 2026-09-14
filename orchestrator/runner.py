"""Start a workflow for one project: budget governor, idempotent run row, checkpointed graph, run status."""
from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from contracts.project import Project
from orchestrator import budget, notify
from orchestrator.checkpointer import checkpointer
from orchestrator.graphs.state import RunState
from orchestrator.runs import RunHandle, finish_run, open_run
from orchestrator.runtime import Runtime

WORKFLOWS: dict[str, Any] = {}
HALT_EXEMPT = {"daily_probe", "post_deploy_audit"}   # the probes are how a halt gets lifted


def register_workflow(name: str, builder) -> None:
    WORKFLOWS[name] = builder


def _load_workflows() -> None:
    if WORKFLOWS:
        return
    from orchestrator.graphs import daily, monthly_full, post_deploy_audit, weekly_monitor

    register_workflow(post_deploy_audit.WORKFLOW, post_deploy_audit.build)
    register_workflow(weekly_monitor.WORKFLOW, weekly_monitor.build)
    register_workflow(monthly_full.WORKFLOW, monthly_full.build)
    register_workflow("daily_probe", daily.build_daily_probe)
    register_workflow("daily_collect", daily.build_daily_collect)
    from orchestrator.graphs import content_pipeline, quarterly_keyword

    register_workflow(quarterly_keyword.WORKFLOW, quarterly_keyword.build)
    register_workflow(content_pipeline.WORKFLOW, content_pipeline.build)


def run_workflow(rt: Runtime, project: Project, workflow: str, trigger: str, logical_date: str,
                 params: dict[str, Any] | None = None, use_memory_checkpointer: bool = False) -> RunHandle:
    _load_workflows()
    params = params or {}
    if not project.active:
        raise RuntimeError(f"{project.slug} is inactive")
    resume = False
    with rt.scope(project.id) as s:
        halted = s.fetchone("select halted_reason from projects where id = %(project_id)s")["halted_reason"]
        if halted and workflow not in HALT_EXEMPT:
            # Section 14: a protected route was indexable. Nothing else runs until a probe comes back clean.
            handle = open_run(s, workflow, trigger, logical_date, status="skipped", halt_reason=f"project halted: {halted}")
            s.audit("orchestrator", "run_skipped_project_halted", {"workflow": workflow, "reason": halted}, handle.id)
            handle.status = "skipped"
            return handle
        handle = open_run(s, workflow, trigger, logical_date)
        if not handle.created:
            stale = s.fetchone("select status, started_at < now() - interval '2 hours' as stale from runs where project_id = %(project_id)s and id = %(id)s", {"id": handle.id})
            if handle.status in ("halted_budget", "skipped"):
                s.execute("update runs set status = 'running', halt_reason = null, started_at = now(), ended_at = null where project_id = %(project_id)s and id = %(id)s",
                          {"id": handle.id})
                handle.status = "running"
            elif handle.status == "failed" or (handle.status == "running" and stale["stale"]):
                # P6: resume from the checkpoint; the graph picks up at the node that failed
                resume = True
                s.execute("update runs set status = 'running', halt_reason = null, ended_at = null where project_id = %(project_id)s and id = %(id)s", {"id": handle.id})
                s.audit("orchestrator", "run_resumed", {"workflow": workflow, "logical_date": logical_date, "previous_status": handle.status}, handle.id)
                handle.status = "running"
            else:
                s.audit("orchestrator", "duplicate_trigger_skipped", {"workflow": workflow, "logical_date": logical_date}, handle.id)
                return handle
        # Budget governor: before dispatch, never after. The run row exists so the halt is durable and visible.
        decision = budget.decide(s, workflow, project.monthly_cost_cap_usd)
        if not decision.allowed:
            s.execute("update runs set status = 'halted_budget', ended_at = now(), halt_reason = %(reason)s where project_id = %(project_id)s and id = %(id)s",
                      {"reason": decision.reason, "id": handle.id})
            s.audit("budget_governor", "run_halted_budget", {"workflow": workflow, "reason": decision.reason}, handle.id)
            notify.send(s, f"[{project.slug}] {workflow} halted by budget governor", decision.reason, {"severity": "budget"}, handle.id)
            handle.status = "halted_budget"
            return handle
        s.audit("budget_governor", "run_dispatched", {"workflow": workflow, "projected_usd": decision.projected_usd, "remaining_usd": decision.remaining_usd}, handle.id)

    graph = WORKFLOWS[workflow](rt)
    initial: RunState = {"project_id": str(project.id), "run_id": str(handle.id), "params": params, "collectors": {},
                         "artifacts": {}, "gate": {}, "issues": [], "approvals": [], "errors": [], "status": "running"}
    config = {"configurable": {"thread_id": str(handle.id)}}
    try:
        if use_memory_checkpointer:
            final = graph.compile(checkpointer=MemorySaver()).invoke(initial, config)
        else:
            with checkpointer(rt.db_url) as cp:
                compiled = graph.compile(checkpointer=cp)
                has_checkpoint = resume and compiled.get_state(config).values
                final = compiled.invoke(None if has_checkpoint else initial, config)
    except Exception as e:
        with rt.scope(project.id) as s:
            finish_run(s, handle.id, "failed", f"{type(e).__name__}: {e}"[:500])
            notify.send(s, f"[{project.slug}] {workflow} failed", f"{type(e).__name__}: {e}", {"severity": "failure"}, handle.id)
        handle.status = "failed"
        raise
    status = final.get("status") or "done"
    with rt.scope(project.id) as s:
        finish_run(s, handle.id, status)
    handle.status = status
    return handle
