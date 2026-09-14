"""Inbound webhooks. Vercel deploy -> post_deploy_audit, keyed on the deployment id so a redelivery is a no-op."""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

from db.connection import connect, list_active_projects
from orchestrator.runner import run_workflow
from orchestrator.runtime import Runtime


def verify_vercel_signature(body: bytes, signature: str | None, secret: str | None) -> bool:
    if not secret:
        return False
    if not signature:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
    return hmac.compare_digest(digest, signature)


def handle_vercel_deploy(rt: Runtime, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("type") not in ("deployment.succeeded", "deployment.ready"):
        return {"ignored": "event type"}
    data = payload.get("payload", {})
    deployment = data.get("deployment", {})
    if (data.get("target") or deployment.get("target")) != "production":
        return {"ignored": "not production"}
    deployment_id = deployment.get("id") or data.get("deploymentId")
    if not deployment_id:
        return {"ignored": "no deployment id"}
    name = (data.get("project", {}) or {}).get("name") or data.get("name") or ""
    aliases = {a.lower() for a in (data.get("alias") or deployment.get("alias") or [])}
    url = (deployment.get("url") or data.get("url") or "").lower()
    with connect(rt.db_url) as conn:
        projects = list_active_projects(conn)
    from contracts.project import Project

    match = None
    for row in projects:
        p = Project.from_row(row)
        if p.slug == name or any(d.lower() in aliases or d.lower() == url for d in p.domains):
            match = p
            break
    if match is None:
        return {"ignored": f"no project matches deployment {name!r}"}
    changed = [u for u in (data.get("changed_urls") or []) if isinstance(u, str)]
    handle = run_workflow(rt, match, "post_deploy_audit", "vercel_webhook", f"deploy:{deployment_id}", {"changed_urls": changed})
    return {"project": match.slug, "run_id": str(handle.id), "status": handle.status, "created": handle.created}
