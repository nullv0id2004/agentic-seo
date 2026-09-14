from __future__ import annotations

from typing import Any, TypedDict


class RunState(TypedDict, total=False):
    project_id: str
    run_id: str
    params: dict[str, Any]
    collectors: dict[str, dict[str, Any]]
    artifacts: dict[str, dict[str, Any]]
    gate: dict[str, dict[str, Any]]
    issues: list[str]
    approvals: list[str]
    critical_violations: list[dict[str, Any]]
    report_id: str | None
    errors: list[str]
    status: str
