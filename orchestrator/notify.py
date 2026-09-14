"""Notifications. Digest per project per day; criticals break through immediately.

The transport is pluggable (email / webhook). Without one configured, notifications are written to
the audit log only, which is still a durable record the console shows.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any
from uuid import UUID

from db.connection import ProjectScope

Transport = Callable[[str, str, dict[str, Any]], None]   # (subject, body, meta)

_transports: list[Transport] = []


def add_transport(t: Transport) -> None:
    _transports.append(t)


def _stdout_transport(subject: str, body: str, meta: dict[str, Any]) -> None:
    if os.environ.get("SEO_NOTIFY_STDOUT", "1") == "1":
        print(f"[notify] {subject}\n{body}")


def send(scope: ProjectScope, subject: str, body: str, meta: dict[str, Any] | None = None, run_id: UUID | None = None) -> None:
    meta = meta or {}
    for t in _transports or [_stdout_transport]:
        try:
            t(subject, body, meta)
        except Exception as e:  # a failed notification is logged, never fatal to the run
            scope.audit("notify", "notify_failed", {"subject": subject, "error": str(e)}, run_id)
    scope.audit("notify", "notified", {"subject": subject, "meta": meta}, run_id)


def page_owner(scope: ProjectScope, run_id: UUID, violations: list[dict[str, Any]]) -> None:
    """A critical rule failed after a deploy. This breaks through the digest."""
    slug = scope.fetchone("select slug from projects where id = %(project_id)s")["slug"]
    subject = f"[{slug}] CRITICAL: {len(violations)} protected route(s) reachable by crawlers"
    body = "The header probe found routes that violate a critical rule. Fix before anything else.\n" + json.dumps(
        [{"rule": v["rule_key"], "assertion": v["assertion"], "detail": v["detail"]} for v in violations], indent=1)
    send(scope, subject, body, {"severity": "critical", "slug": slug}, run_id)


def daily_digest(scope: ProjectScope) -> dict[str, Any] | None:
    """Build and send one digest for pending, un-notified, non-critical approvals. Returns the digest or None."""
    rows = scope.fetchall(
        "select id, action_type, summary_plain_english, requested_by_agent, created_at from approvals "
        "where project_id = %(project_id)s and status = 'pending' and notified_at is null and severity <> 'critical' order by created_at"
    )
    if not rows:
        return None
    slug = scope.fetchone("select slug from projects where id = %(project_id)s")["slug"]
    lines = [f"- [{r['action_type']}] {r['summary_plain_english']} (from {r['requested_by_agent']})" for r in rows]
    body = f"{len(rows)} approval(s) waiting for {slug}:\n" + "\n".join(lines)
    send(scope, f"[{slug}] Daily approvals digest: {len(rows)} pending", body, {"slug": slug, "count": len(rows)})
    scope.execute("update approvals set notified_at = now() where project_id = %(project_id)s and id = any(%(ids)s)", {"ids": [r["id"] for r in rows]})
    return {"slug": slug, "count": len(rows), "body": body}
