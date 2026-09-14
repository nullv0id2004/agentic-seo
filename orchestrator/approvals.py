"""Approval queue. Every approval carries its reversal or it is not queued (the table constraint enforces it)."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from db.connection import ProjectScope

APPROVAL_ACTIONS = {
    "open_fix_pr": {"kind": "close_pr_and_delete_branch"},
    "publish_content": {"kind": "unpublish"},
    "send_pitch": {"kind": "send_retraction"},
    "robots_change": {"kind": "restore_previous_robots"},
    "sitemap_change": {"kind": "restore_previous_sitemap"},
    "canonical_change": {"kind": "restore_previous_canonical"},
    "hreflang_change": {"kind": "restore_previous_hreflang"},
    "spend_above_cap": {"kind": "no_op_reject"},
    "page_owner": {"kind": "acknowledge"},
    "keyword_mapping": {"kind": "restore_previous_mapping"},
}


class ReversalMissing(ValueError):
    pass


def queue_approval(scope: ProjectScope, run_id: UUID, action_type: str, payload: dict[str, Any], summary: str,
                   requested_by: str, reversal_payload: dict[str, Any], severity: str = "normal") -> UUID:
    if action_type not in APPROVAL_ACTIONS:
        raise ValueError(f"unknown approval action {action_type!r}")
    if not reversal_payload or "kind" not in reversal_payload:
        raise ReversalMissing(f"{action_type}: an action that cannot describe its own reversal cannot be queued")
    aid = scope.insert("approvals", {
        "run_id": run_id, "action_type": action_type, "payload": payload, "summary_plain_english": summary,
        "requested_by_agent": requested_by, "reversal_payload": reversal_payload, "severity": severity,
    })
    scope.audit(requested_by, "approval_queued", {"approval_id": str(aid), "action_type": action_type, "severity": severity}, run_id)
    return aid


def decide(scope: ProjectScope, approval_id: UUID, approver_id: UUID, approve: bool) -> dict[str, Any]:
    row = scope.fetchone("select * from approvals where project_id = %(project_id)s and id = %(id)s", {"id": approval_id})
    if not row or row["status"] != "pending":
        raise ValueError("approval is not pending")
    project = scope.fetchone("select approver_id from projects where id = %(project_id)s")
    if project["approver_id"] != approver_id:
        raise PermissionError("only the project's approver may decide")
    status = "approved" if approve else "rejected"
    scope.execute("update approvals set status = %(status)s, approver_id = %(approver)s, decided_at = now() where project_id = %(project_id)s and id = %(id)s",
                  {"status": status, "approver": approver_id, "id": approval_id})
    scope.audit(f"approver:{approver_id}", f"approval_{status}", {"approval_id": str(approval_id)}, row["run_id"])
    return {**row, "status": status}


def pending(scope: ProjectScope) -> list[dict[str, Any]]:
    return scope.fetchall("select * from approvals where project_id = %(project_id)s and status = 'pending' order by severity desc, created_at")
