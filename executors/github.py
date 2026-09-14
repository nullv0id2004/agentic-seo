"""github_executor: opens a PR on a fix branch. Never pushes to the default branch. Never merges.

One credential: the secret named github-fix-branch-token (contents:write, pull_requests:write on the
project's repo only). Resolved at call time, never stored.

Payload forms:
  {file_changes: [{path, content}], title, body}   concrete edits (robots, sitemap, canonical, hreflang)
  {claude_code_prompt, issue_type, url, recommended_fix}   a fix note committed under .seo/fixes/ for a
                                                            coding agent or developer to act on in the PR
Reversal: close the PR and delete the branch. Prior file contents are recorded in the reversal payload so
the change is also describable as a revert.
"""
from __future__ import annotations

import base64
from typing import Any

import httpx

from config.secrets import resolve_secret
from db.connection import ProjectScope
from executors.base import ExecutionResult, require_approved, require_executed

API = "https://api.github.com"
SECRET = "github-fix-branch-token"
ACTION_TYPES = ("open_fix_pr", "robots_change", "sitemap_change", "canonical_change", "hreflang_change")
FORBIDDEN_ENDPOINT_FRAGMENTS = ("/merge", "/merges")


class GitHubExecutor:
    action_types = ACTION_TYPES

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def _client(self) -> httpx.Client:
        token = resolve_secret(SECRET)
        return httpx.Client(base_url=API, transport=self.transport, timeout=30,
                            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "seo-agents-executor"})

    def _call(self, http: httpx.Client, method: str, path: str, **kw) -> httpx.Response:
        if any(f in path for f in FORBIDDEN_ENDPOINT_FRAGMENTS):
            raise PermissionError("github_executor never merges (Section 13.7)")
        r = http.request(method, path, **kw)
        return r

    def execute(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult:
        require_approved(approval)
        repo = project.get("github_repo")
        if not repo:
            raise RuntimeError(f"{project.get('slug')} has no github_repo configured")
        payload = approval["payload"]
        branch = f"seo-fix/{str(approval['id'])[:8]}"
        with self._client() as http:
            meta = self._call(http, "GET", f"/repos/{repo}")
            meta.raise_for_status()
            base = meta.json()["default_branch"]
            if branch == base:
                raise PermissionError("fix branch may not be the default branch")
            ref = self._call(http, "GET", f"/repos/{repo}/git/ref/heads/{base}")
            ref.raise_for_status()
            sha = ref.json()["object"]["sha"]
            r = self._call(http, "POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})
            r.raise_for_status()
            changes = payload.get("file_changes") or [{
                "path": f".seo/fixes/{str(approval['id'])[:8]}-{payload.get('issue_type', 'fix')}.md",
                "content": _fix_note(payload),
            }]
            prior: list[dict[str, Any]] = []
            for ch in changes:
                existing = self._call(http, "GET", f"/repos/{repo}/contents/{ch['path']}", params={"ref": branch})
                file_sha = None
                if existing.status_code == 200:
                    body = existing.json()
                    file_sha = body.get("sha")
                    prior.append({"path": ch["path"], "content": base64.b64decode(body.get("content") or "").decode("utf-8", "replace")})
                else:
                    prior.append({"path": ch["path"], "content": None})
                put = {"message": f"seo: {payload.get('issue_type') or approval['action_type']} ({str(approval['id'])[:8]})",
                       "content": base64.b64encode(ch["content"].encode()).decode(), "branch": branch}
                if file_sha:
                    put["sha"] = file_sha
                self._call(http, "PUT", f"/repos/{repo}/contents/{ch['path']}", json=put).raise_for_status()
            title = payload.get("title") or f"SEO fix: {payload.get('issue_type') or approval['action_type']}"
            body = payload.get("body") or _pr_body(approval)
            pr = self._call(http, "POST", f"/repos/{repo}/pulls", json={"title": title, "head": branch, "base": base, "body": body})
            pr.raise_for_status()
            number = pr.json()["number"]
        reversal = {"kind": "close_pr_and_delete_branch", "repo": repo, "pr_number": number, "branch": branch, "prior_files": prior}
        return ExecutionResult(ok=True, detail={"pr_number": number, "branch": branch, "url": pr.json().get("html_url")}, reversal_payload=reversal)

    def reverse(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult:
        require_executed(approval)
        rp = approval["reversal_payload"]
        repo, number, branch = rp["repo"], rp["pr_number"], rp["branch"]
        with self._client() as http:
            self._call(http, "PATCH", f"/repos/{repo}/pulls/{number}", json={"state": "closed"}).raise_for_status()
            d = self._call(http, "DELETE", f"/repos/{repo}/git/refs/heads/{branch}")
            if d.status_code not in (204, 404, 422):
                d.raise_for_status()
        return ExecutionResult(ok=True, detail={"closed_pr": number, "deleted_branch": branch}, reversal_payload=rp)


def _fix_note(payload: dict[str, Any]) -> str:
    return (f"# SEO fix: {payload.get('issue_type', 'issue')}\n\n"
            f"URL: {payload.get('url') or 'protected route (see the cited raw row)'}\n\n"
            f"## Recommended fix\n{payload.get('recommended_fix', '')}\n\n"
            f"## Prompt for a coding agent\n{payload.get('claude_code_prompt', '')}\n")


def _pr_body(approval: dict[str, Any]) -> str:
    p = approval["payload"]
    return (f"{approval['summary_plain_english']}\n\n"
            f"Evidence ref: `{p.get('evidence_ref') or p.get('issue_id') or ''}`\n\n"
            f"Opened by the SEO worker from approval `{approval['id']}`. This PR is never merged automatically.")
