"""Run registry: durable, idempotent run rows."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import psycopg

from db.connection import ProjectScope

TERMINAL = {"done", "failed", "halted_budget", "skipped"}


def idempotency_key(project_id: UUID, workflow: str, logical_date: str) -> str:
    return hashlib.sha256(f"{project_id}|{workflow}|{logical_date}".encode()).hexdigest()


@dataclass
class RunHandle:
    id: UUID
    project_id: UUID
    workflow: str
    idempotency_key: str
    status: str
    created: bool           # False when the row already existed (duplicate trigger)


def open_run(scope: ProjectScope, workflow: str, trigger: str, logical_date: str, status: str = "running",
             halt_reason: str | None = None) -> RunHandle:
    """Insert the run row. A duplicate (same project, workflow, logical date) is returned with created=False.

    Uses a savepoint so the unique-violation does not abort the enclosing transaction.
    """
    key = idempotency_key(scope.project_id, workflow, logical_date)
    try:
        with scope.conn.transaction():
            rid = scope.insert("runs", {
                "workflow": workflow, "trigger": trigger, "idempotency_key": key, "status": status,
                "logical_date": _as_date(logical_date), "halt_reason": halt_reason,
            })
        return RunHandle(rid, scope.project_id, workflow, key, status, True)
    except psycopg.errors.UniqueViolation:
        row = scope.fetchone(
            "select id, status from runs where project_id = %(project_id)s and workflow = %(workflow)s and idempotency_key = %(key)s",
            {"workflow": workflow, "key": key},
        )
        return RunHandle(row["id"], scope.project_id, workflow, key, row["status"], False)


def finish_run(scope: ProjectScope, run_id: UUID, status: str, halt_reason: str | None = None) -> None:
    scope.execute(
        """update runs set status = %(status)s, ended_at = now(), halt_reason = coalesce(%(halt_reason)s, halt_reason),
             cost_usd = coalesce((select sum(cost_usd) from agent_logs where project_id = %(project_id)s and run_id = %(run_id)s), 0),
             tokens_in = coalesce((select sum(tokens_in) from agent_logs where project_id = %(project_id)s and run_id = %(run_id)s), 0),
             tokens_out = coalesce((select sum(tokens_out) from agent_logs where project_id = %(project_id)s and run_id = %(run_id)s), 0)
           where project_id = %(project_id)s and id = %(run_id)s""",
        {"status": status, "halt_reason": halt_reason, "run_id": run_id},
    )


def log_agent(scope: ProjectScope, run_id: UUID, agent: str, node: str, status: str, output: dict[str, Any] | None = None,
              inputs_digest: str | None = None, model: str | None = None, tokens_in: int = 0, tokens_out: int = 0,
              cost_usd: float = 0.0, retries: int = 0) -> None:
    scope.insert("agent_logs", {
        "run_id": run_id, "agent": agent, "node": node, "status": status, "output": output or {},
        "inputs_digest": inputs_digest, "model": model, "tokens_in": tokens_in, "tokens_out": tokens_out,
        "cost_usd": cost_usd, "retries": retries,
    })


def _as_date(logical: str) -> date | None:
    try:
        return date.fromisoformat(logical[:10])
    except ValueError:
        return None
