"""Persist gated artifacts. Stage 1 result -> rows in the derived tables the artifact targets."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from db.connection import ProjectScope


def write_issues(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> list[UUID]:
    ids: list[UUID] = []
    for issue in artifact.get("issues", []):
        ids.append(scope.insert("issues", {
            "run_id": run_id, "url": issue.get("url"), "issue_type": issue["issue_type"], "severity": issue["severity"],
            "evidence": issue["evidence"], "evidence_ref": issue.get("evidence_ref"),
            "recommended_fix": issue.get("recommended_fix"), "claude_code_prompt": issue.get("claude_code_prompt"),
        }))
    return ids
