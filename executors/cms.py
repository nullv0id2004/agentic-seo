"""cms_executor: publishes an approved draft. One credential: cms-content-write-token, scoped to the
content collection. POST {cms_publish_url} with the brief; reversal is DELETE on the published url."""
from __future__ import annotations

from typing import Any

import httpx

from config.secrets import resolve_secret
from db.connection import ProjectScope
from executors.base import ExecutionResult, require_approved, require_executed

SECRET = "cms-content-write-token"


class CMSExecutor:
    action_types = ("publish_content",)

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def execute(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult:
        require_approved(approval)
        url = project.get("cms_publish_url")
        if not url:
            raise RuntimeError(f"{project.get('slug')} has no cms_publish_url configured")
        brief_id = approval["payload"]["brief_id"]
        brief = scope.fetchone("select id, title, answer_block, outline, draft, sources, status from content_briefs where project_id = %(project_id)s and id = %(id)s", {"id": brief_id})
        if not brief:
            raise RuntimeError("brief not found")
        token = resolve_secret(SECRET)
        with httpx.Client(transport=self.transport, timeout=30, headers={"Authorization": f"Bearer {token}"}) as http:
            r = http.post(url, json={"title": brief["title"], "answer_block": brief["answer_block"], "outline": brief["outline"],
                                     "body": brief["draft"], "sources": brief["sources"], "external_id": str(brief_id)})
            r.raise_for_status()
            published_url = r.json().get("url")
        scope.execute("update content_briefs set status = 'published', published_url = %(url)s, approved_by = %(by)s where project_id = %(project_id)s and id = %(id)s",
                      {"url": published_url, "by": approval.get("approver_id"), "id": brief_id})
        return ExecutionResult(ok=True, detail={"published_url": published_url},
                               reversal_payload={"kind": "unpublish", "brief_id": str(brief_id), "published_url": published_url, "unpublish_url": r.json().get("unpublish_url") or published_url})

    def reverse(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult:
        require_executed(approval)
        rp = approval["reversal_payload"]
        token = resolve_secret(SECRET)
        with httpx.Client(transport=self.transport, timeout=30, headers={"Authorization": f"Bearer {token}"}) as http:
            http.delete(rp["unpublish_url"]).raise_for_status()
        scope.execute("update content_briefs set status = 'unpublished', published_url = null where project_id = %(project_id)s and id = %(id)s", {"id": rp["brief_id"]})
        return ExecutionResult(ok=True, detail={"unpublished": rp["published_url"]}, reversal_payload=rp)
