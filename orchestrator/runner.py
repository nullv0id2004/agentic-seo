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


def register_workflow(name: str, builder) -> None:
    WORKFLOWS[name] = builder


def _load_workflows() -> None:
    if WORKFLOWS:
        return
    from orchestrator.graphs import post_deploy_audit

    register_workflow(post_deploy_audit.WORKFLOW, post_deploy_audit.build)
    try:
        from orchestrator.graphs import content_pipeline, monthly_full, quarterly_keyword, weekly_monitor

        for mod in (weekly_monitor, monthly_full, quarterly_keyword, content_pipeline):
            register_workflow(mod.WORKFLOW, mod.build)
    except ImportError:
        pass


def run_workflow(rt: Runtime, project: Project, workflow: str, trigger: str, logical_date: str,
                 params: dict[str, Any] | None = None, use_memory_checkpointer: bool = False) -> RunHandle:
    _load_workflows()
    params = params or {}
    if not project.active:
        raise RuntimeError(f"{project.slug} is inactive")
    with rt.scope(project.id) as s:
        handle = open_run(s, workflow, trigger, logical_date)
        if not handle.created:
            if handle.status == "halted_budget":
                s.execute("update runs set status = 'running', halt_reason = null, started_at = now() where project_id = %(project_id)s and id = %(id)s",
                          {"id": handle.id})
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
                final = graph.compile(checkpointer=cp).invoke(initial, config)
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
