"""Project-scoped Postgres access.

Two rules, both enforced here rather than by convention:

1. Every transaction sets app.project_id before any statement runs, so RLS resolves to a
   single project. A connection that never sets it sees zero rows.
2. Every query the worker issues also carries an explicit project_id predicate. RLS is the
   backstop, not the only line. `ProjectScope.execute` refuses statements against
   tenant tables that do not mention project_id.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

TENANT_TABLES = frozenset({
    "brand_rules", "critical_rules", "raw_gsc_performance", "raw_ga4_daily", "raw_crawl_pages", "raw_sitemap_urls",
    "raw_vitals", "raw_serp", "raw_keyword_metrics", "raw_fetched_documents", "raw_search_status",
    "collection_gaps", "keywords", "pages", "issues", "content_briefs", "mentions", "pitches", "reports",
    "runs", "gate_results", "approvals", "agent_logs", "audit_log",
})

RAW_TABLES = frozenset(t for t in TENANT_TABLES if t.startswith("raw_"))

_TABLE_RE = re.compile(r"\b(?:from|into|update|join)\s+([a-z_]+)", re.IGNORECASE)


def jsonb(value: Any) -> Json:
    """Wrap a list (or anything) that must be stored as jsonb rather than as a Postgres array."""
    return Json(value)


class UnscopedQueryError(RuntimeError):
    """Raised when a statement against a tenant table carries no project_id predicate."""


class RawTableWriteError(RuntimeError):
    """Raised when something that is not a collector tries to write a raw_* table (Section 13.4)."""


def database_url() -> str:
    url = os.environ.get("SEO_DATABASE_URL")
    if not url:
        raise RuntimeError("SEO_DATABASE_URL is not set; failing closed")
    return url


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or database_url(), row_factory=dict_row)


def _tables_in(sql: str) -> set[str]:
    return {m.lower() for m in _TABLE_RE.findall(sql)}


def _is_write(sql: str) -> bool:
    head = sql.lstrip()[:12].lower()
    return head.startswith(("insert", "update", "delete"))


class ProjectScope:
    """A transaction bound to exactly one project.

    `caller` names the component issuing statements. Only callers registered as collectors
    may write raw_* tables; the check is mechanical, not by prompt.
    """

    def __init__(self, conn: psycopg.Connection, project_id: UUID, caller: str = "worker"):
        self.conn = conn
        self.project_id = project_id
        self.caller = caller

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> psycopg.Cursor:
        tables = _tables_in(sql)
        if tables & TENANT_TABLES and "project_id" not in sql:
            raise UnscopedQueryError(f"statement touches {sorted(tables & TENANT_TABLES)} without project_id: {sql[:120]}")
        if _is_write(sql) and (tables & RAW_TABLES) and not self.caller.startswith("collector:"):
            raise RawTableWriteError(f"{self.caller} may not write {sorted(tables & RAW_TABLES)} (Section 13.4)")
        merged = {"project_id": self.project_id, **(params or {})}
        # dicts are always jsonb; lists are Postgres arrays unless the caller wrapped them with jsonb()
        merged = {k: (Json(v) if isinstance(v, dict) else v) for k, v in merged.items()}
        return self.conn.execute(sql, merged)

    def fetchall(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return list(self.execute(sql, params).fetchall())

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return self.execute(sql, params).fetchone()

    def insert(self, table: str, row: dict[str, Any], returning: str = "id") -> Any:
        row = {"project_id": self.project_id, **row}
        cols = ", ".join(row.keys())
        vals = ", ".join(f"%({k})s" for k in row)
        cur = self.execute(f"insert into {table} ({cols}) values ({vals}) returning {returning}", row)
        got = cur.fetchone()
        return got[returning] if got else None

    def audit(self, actor: str, event: str, detail: dict[str, Any] | None = None, run_id: UUID | None = None) -> None:
        self.insert("audit_log", {"actor": actor, "event": event, "detail": detail or {}, "run_id": run_id})


@contextmanager
def project_scope(project_id: UUID, caller: str = "worker", conn: psycopg.Connection | None = None,
                  url: str | None = None) -> Iterator[ProjectScope]:
    """Open a transaction bound to one project. Commits on success, rolls back on error."""
    own = conn is None
    c = conn or connect(url)
    try:
        with c.transaction():
            c.execute("select set_config('app.project_id', %s, true)", (str(project_id),))
            yield ProjectScope(c, project_id, caller)
    finally:
        if own:
            c.close()


def list_active_projects(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """The one unscoped read: the scheduler's list of projects to iterate over."""
    return list(conn.execute("select * from list_active_projects()").fetchall())
