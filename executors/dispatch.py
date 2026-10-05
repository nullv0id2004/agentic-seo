"""Run approved actions and reverse executed ones. The only code path from an approvals row to the world."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from db.connection import ProjectScope
from executors.base import Executor
from executors.cms import CMSExecutor
from executors.email import EmailExecutor
from executors.github import GitHubExecutor
from executors.internal import AcknowledgeExecutor, KeywordMappingExecutor
from orchestrator.approvals import APPROVAL_ACTIONS

MAX_ATTEMPTS = 3   # an approved action that fails this many times is marked failed and no longer retried


def default_executors() -> dict[str, Executor]:
    table: dict[str, Executor] = {}
    for ex in (GitHubExecutor(), CMSExecutor(), EmailExecutor(), KeywordMappingExecutor(), AcknowledgeExecutor()):
        for a in ex.action_types:
            table[a] = ex
    missing = set(APPROVAL_ACTIONS) - set(table)
    assert not missing, f"approval actions without an executor: {missing}"
    return table


def _executor_agent(action_type: str) -> str:
    return {"open_fix_pr": "github", "robots_change": "github", "sitemap_change": "github", "canonical_change": "github", "hreflang_change": "github",
            "publish_content": "cms", "send_pitch": "email", "keyword_mapping": "keyword_mapping"}.get(action_type, "orchestrator")


def execute_one(rt, project_id: UUID, approval_id: UUID, executors: dict[str, Executor] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    executors = executors or default_executors()
    with rt.scope(project_id) as s:
        approval = s.fetchone("select * from approvals where project_id = %(project_id)s and id = %(id)s", {"id": approval_id})
        project = s.fetchone("select * from projects where id = %(project_id)s")
    if not approval:
        raise ValueError("approval not found")
    ex = executors[approval["action_type"]]
    try:
        with rt.scope(project_id, _executor_agent(approval["action_type"])) as s:
            result = ex.execute(s, project, approval, params or {})
    except Exception as e:
        # The executor's transaction rolled back. Record the failure in its own transaction. The row stays
        # approved for another attempt on a later tick, up to MAX_ATTEMPTS; then it is marked failed so the
        # scheduler stops retrying a failure that will not fix itself (a pitch with no recipient, say).
        prior = approval.get("execution_result") or {}
        attempts = int(prior.get("attempts") or (1 if prior.get("ok") is False else 0)) + 1
        error = f"{type(e).__name__}: {e}"
        gave_up = attempts >= MAX_ATTEMPTS
        with rt.scope(project_id) as s:
            s.audit(f"executor:{approval['action_type']}", "execution_failed",
                    {"approval_id": str(approval_id), "error": error, "attempt": attempts, "gave_up": gave_up}, approval["run_id"])
            s.execute("update approvals set execution_result = %(res)s, status = case when %(gave_up)s then 'failed' else status end"
                      " where project_id = %(project_id)s and id = %(id)s",
                      {"res": {"ok": False, "error": error[:500], "attempts": attempts}, "gave_up": gave_up, "id": approval_id})
        raise
    with rt.scope(project_id) as s:
        s.execute(
            "update approvals set status = 'executed', executed_at = now(), execution_result = %(res)s, reversal_payload = %(rev)s where project_id = %(project_id)s and id = %(id)s",
            {"res": {"ok": True, "detail": result["detail"]}, "rev": result["reversal_payload"], "id": approval_id})
        s.audit(f"executor:{approval['action_type']}", "executed", {"approval_id": str(approval_id), "detail": result["detail"]}, approval["run_id"])
    return result


def reverse_one(rt, project_id: UUID, approval_id: UUID, executors: dict[str, Executor] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    executors = executors or default_executors()
    with rt.scope(project_id) as s:
        approval = s.fetchone("select * from approvals where project_id = %(project_id)s and id = %(id)s", {"id": approval_id})
        project = s.fetchone("select * from projects where id = %(project_id)s")
    if not approval:
        raise ValueError("approval not found")
    ex = executors[approval["action_type"]]
    with rt.scope(project_id, _executor_agent(approval["action_type"])) as s:
        result = ex.reverse(s, project, approval, params or {})
        s.execute("update approvals set status = 'reversed', reversed_at = now() where project_id = %(project_id)s and id = %(id)s", {"id": approval_id})
        s.audit(f"executor:{approval['action_type']}", "reversed", {"approval_id": str(approval_id), "detail": result["detail"]}, approval["run_id"])
    return result


def execute_approved(rt, project_id: UUID, executors: dict[str, Executor] | None = None) -> list[dict[str, Any]]:
    """Execute every approved row for a project, oldest first. A failure is recorded; the scheduler calls this every
    minute, so a failing row is retried on later ticks until execute_one marks it failed after MAX_ATTEMPTS."""
    with rt.scope(project_id) as s:
        rows = s.fetchall("select id, action_type from approvals where project_id = %(project_id)s and status = 'approved' order by created_at")
    out = []
    for r in rows:
        try:
            res = execute_one(rt, project_id, r["id"], executors)
            out.append({"approval_id": str(r["id"]), "action_type": r["action_type"], "ok": True, "detail": res["detail"]})
        except Exception as e:
            out.append({"approval_id": str(r["id"]), "action_type": r["action_type"], "ok": False, "error": f"{type(e).__name__}: {e}"})
    with rt.scope(project_id) as s:
        close_settled_runs(s)
    return out


def close_settled_runs(scope: ProjectScope) -> int:
    """A run paused for approval is finished once none of its approvals is still pending or approved
    but unexecuted. Before this the first keyword run sat at paused_for_approval after its mapping
    had been executed, which read as "nothing happened"."""
    cur = scope.execute(
        """update runs set status = 'done', ended_at = now()
            where project_id = %(project_id)s and status = 'paused_for_approval'
              and not exists (select 1 from approvals a where a.project_id = runs.project_id and a.run_id = runs.id
                                and a.status in ('pending', 'approved'))""")
    n = cur.rowcount
    if n:
        scope.audit("executor", "runs_completed_after_approvals", {"count": n})
    return n


def _scope_for(rt, project_id: UUID, action_type: str) -> ProjectScope:  # pragma: no cover - helper for callers that need a raw scope
    return rt.scope(project_id, _executor_agent(action_type))
