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
        # the executor's transaction rolled back; record the failure in its own transaction and leave the row approved
        with rt.scope(project_id) as s:
            s.audit(f"executor:{approval['action_type']}", "execution_failed", {"approval_id": str(approval_id), "error": f"{type(e).__name__}: {e}"}, approval["run_id"])
            s.execute("update approvals set execution_result = %(res)s where project_id = %(project_id)s and id = %(id)s",
                      {"res": {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}, "id": approval_id})
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
    """Execute every approved row for a project, oldest first. Failures are recorded and skipped, not retried here."""
    with rt.scope(project_id) as s:
        rows = s.fetchall("select id, action_type from approvals where project_id = %(project_id)s and status = 'approved' order by created_at")
    out = []
    for r in rows:
        try:
            res = execute_one(rt, project_id, r["id"], executors)
            out.append({"approval_id": str(r["id"]), "action_type": r["action_type"], "ok": True, "detail": res["detail"]})
        except Exception as e:
            out.append({"approval_id": str(r["id"]), "action_type": r["action_type"], "ok": False, "error": f"{type(e).__name__}: {e}"})
    return out


def _scope_for(rt, project_id: UUID, action_type: str) -> ProjectScope:  # pragma: no cover - helper for callers that need a raw scope
    return rt.scope(project_id, _executor_agent(action_type))
