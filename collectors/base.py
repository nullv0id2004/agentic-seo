"""Collector contract and the wrapper that makes every failure path a gap row, never a lost run."""
from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from contracts.project import Project
from db.connection import ProjectScope, connect


@dataclass
class CollectorResult:
    collector: str
    rows_written: int = 0
    gaps: int = 0
    partial: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


class CollectorContext:
    """Everything a collector needs: a project-scoped connection and a gap recorder.

    Writes are autocommitted one row at a time so that a network failure half way through a crawl
    leaves every row collected so far in place. The scope is opened with caller="collector:<name>",
    the only caller class allowed to write raw_* tables.
    """

    def __init__(self, name: str, project: Project, run_id: UUID, db_url: str | None = None):
        self.name = name
        self.project = project
        self.run_id = run_id
        self.db_url = db_url
        self.result = CollectorResult(collector=name)
        self.conn = None
        self.scope: ProjectScope | None = None

    def __enter__(self) -> CollectorContext:
        self.conn = connect(self.db_url)
        self.conn.autocommit = True
        self.conn.execute("select set_config('app.project_id', %s, false)", (str(self.project.id),))
        self.scope = ProjectScope(self.conn, self.project.id, caller=f"collector:{self.name}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn is not None:
            self.conn.close()

    def write(self, table: str, row: dict[str, Any]) -> UUID:
        rid = self.scope.insert(table, {"run_id": self.run_id, **row})
        self.result.rows_written += 1
        return rid

    def gap(self, reason: str, affected_scope: str | None = None) -> None:
        self.scope.insert("collection_gaps", {
            "run_id": self.run_id, "collector": self.name, "reason": reason[:2000], "affected_scope": affected_scope,
        })
        self.result.gaps += 1
        self.result.partial = True

    def read(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.scope.fetchall(sql, params)


CollectFn = Callable[[CollectorContext, dict[str, Any]], None]


def run_collector(name: str, fn: CollectFn, project: Project, run_id: UUID, params: dict[str, Any] | None = None,
                  db_url: str | None = None) -> CollectorResult:
    """Run a collector body. Any exception becomes a gap row plus a partial result.

    Rows written before the failure stay written. This is what "killing the network mid-crawl
    produces a gap row and a partial result, never an exception that loses the run" means.
    """
    params = params or {}
    with CollectorContext(name, project, run_id, db_url) as ctx:
        try:
            fn(ctx, params)
        except Exception as e:  # noqa: BLE001 - every failure is a gap, by design
            ctx.gap(f"{type(e).__name__}: {e}", affected_scope=str(params.get("scope") or "run"))
            ctx.result.detail["exception"] = traceback.format_exc(limit=3)
    return ctx.result
