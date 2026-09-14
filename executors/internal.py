"""Executors whose whole effect is a row in this database: keyword mappings and owner acknowledgements."""
from __future__ import annotations

from typing import Any

from db.connection import ProjectScope
from executors.base import ExecutionResult, require_approved, require_executed


class KeywordMappingExecutor:
    action_types = ("keyword_mapping",)

    def execute(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult:
        require_approved(approval)
        previous = []
        for m in approval["payload"]["mappings"]:
            row = scope.fetchone("select mapped_url from keywords where project_id = %(project_id)s and keyword = %(kw)s", {"kw": m["keyword"]})
            previous.append({"keyword": m["keyword"], "mapped_url": row["mapped_url"] if row else None})
            scope.execute(
                """insert into keywords (project_id, keyword, mapped_url, intent, updated_at) values (%(project_id)s, %(kw)s, %(url)s, %(intent)s, now())
                   on conflict (project_id, keyword) do update set mapped_url = excluded.mapped_url, intent = coalesce(excluded.intent, keywords.intent), updated_at = now()""",
                {"kw": m["keyword"], "url": m["mapped_url"], "intent": m.get("intent")})
        return ExecutionResult(ok=True, detail={"mapped": len(previous)}, reversal_payload={"kind": "restore_previous_mapping", "previous": previous})

    def reverse(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult:
        require_executed(approval)
        for m in approval["reversal_payload"]["previous"]:
            scope.execute("update keywords set mapped_url = %(url)s, updated_at = now() where project_id = %(project_id)s and keyword = %(kw)s", {"kw": m["keyword"], "url": m["mapped_url"]})
        return ExecutionResult(ok=True, detail={"restored": len(approval["reversal_payload"]["previous"])}, reversal_payload=approval["reversal_payload"])


class AcknowledgeExecutor:
    """page_owner and spend_above_cap approvals change nothing outside; approving records the acknowledgement."""

    action_types = ("page_owner", "spend_above_cap")

    def execute(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult:
        require_approved(approval)
        scope.audit(f"executor:{approval['action_type']}", "acknowledged", {"approval_id": str(approval["id"])}, approval["run_id"])
        return ExecutionResult(ok=True, detail={"acknowledged": True}, reversal_payload={"kind": "acknowledge", "acknowledged": True})

    def reverse(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult:
        require_executed(approval)
        scope.audit(f"executor:{approval['action_type']}", "acknowledgement_withdrawn", {"approval_id": str(approval["id"])}, approval["run_id"])
        return ExecutionResult(ok=True, detail={"acknowledged": False}, reversal_payload={"kind": "acknowledge", "acknowledged": False})
