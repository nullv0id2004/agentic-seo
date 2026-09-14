from __future__ import annotations

from typing import Any, Protocol

from db.connection import ProjectScope


class NotApproved(PermissionError):
    pass


class ExecutionResult(dict):
    """{ok: bool, detail: ..., reversal_payload: {...}} - the executor returns the concrete reversal it learned while acting."""


class Executor(Protocol):
    action_types: tuple[str, ...]

    def execute(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult: ...

    def reverse(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult: ...


def require_approved(approval: dict[str, Any]) -> None:
    if approval.get("status") != "approved":
        raise NotApproved(f"approval {approval.get('id')} is {approval.get('status')!r}, not approved (Section 13.6)")


def require_executed(approval: dict[str, Any]) -> None:
    if approval.get("status") != "executed":
        raise NotApproved(f"approval {approval.get('id')} is {approval.get('status')!r}; only executed actions can be reversed")
