"""Critical-rule violations: the page-the-owner path and the per-project halt (Section 14, last paragraph).

The indexed count of protected routes must stay at zero. When a header probe or a URL inspection
shows otherwise, every workflow for that project halts. The halt is cleared mechanically by the next
probe that finds nothing, never by a prompt and never by an acknowledgement alone.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from contracts.project import Project
from orchestrator import approvals, notify
from rules.indexability import rule_matches


def indexed_protected_routes(scope) -> list[dict[str, Any]]:
    """Protected paths that URL Inspection reported as indexable, from the latest inspection per url."""
    rules = scope.fetchall("select url_pattern from critical_rules where project_id = %(project_id)s and active and assertion = 'must_noindex'")
    rows = scope.fetchall(
        "select distinct on (url) url, indexable, run_id from raw_crawl_pages where project_id = %(project_id)s and indexable is not null order by url, collected_at desc")
    return [r for r in rows if r["indexable"] and any(rule_matches(x["url_pattern"], r["url"]) for x in rules)]


def handle_violations(rt, project: Project, run_id: UUID, violations: list[dict[str, Any]], source: str) -> UUID | None:
    """Notify, queue a critical approval, and halt the project. Returns the approval id."""
    if not violations:
        return None
    reason = f"{len(violations)} protected route(s) reachable by crawlers ({source}, run {run_id})"
    with rt.scope(project.id) as s:
        notify.page_owner(s, run_id, violations)
        aid = approvals.queue_approval(
            s, run_id, "page_owner", {"violations": violations, "source": source},
            f"{len(violations)} protected route(s) are reachable by crawlers. Every workflow for this project is halted until a probe comes back clean.",
            source, {"kind": "acknowledge"}, severity="critical")
        s.execute("update projects set halted_reason = %(reason)s, halted_at = now() where id = %(project_id)s", {"reason": reason})
        s.audit("orchestrator", "project_halted", {"reason": reason}, run_id)
    return aid


def clear_halt_if_clean(rt, project: Project, run_id: UUID, violations: list[dict[str, Any]]) -> bool:
    """A probe with no violations lifts the halt. Returns True when a halt was cleared."""
    if violations:
        return False
    with rt.scope(project.id) as s:
        row = s.fetchone("select halted_reason from projects where id = %(project_id)s")
        if not row or not row["halted_reason"]:
            return False
        if indexed_protected_routes(s):
            return False
        s.execute("update projects set halted_reason = null, halted_at = null where id = %(project_id)s")
        s.audit("orchestrator", "project_halt_cleared", {"by_run": str(run_id)}, run_id)
        notify.send(s, f"[{project.slug}] protected routes clean again; workflows resumed", "The header probe found no violations. The halt is lifted.", {"severity": "info"}, run_id)
    return True
