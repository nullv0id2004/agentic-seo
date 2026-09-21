"""Inbound webhooks. A production deploy -> post_deploy_audit, keyed on the deployment id so a redelivery is a no-op.

Two shapes: the Vercel webhook (signed with the Vercel-issued secret) and a generic endpoint for any
platform, called from the site's own deploy pipeline with a shared bearer secret:

  POST /webhooks/deploy
  Authorization: Bearer <DEPLOY_WEBHOOK_SECRET>
  {"project": "korum", "deployment_id": "<commit sha or run id>", "changed_urls": ["/about", ...]}
"""
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


def verify_bearer(header: str | None, secret: str | None) -> bool:
    if not secret or not header or not header.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(header[7:].strip(), secret)


def handle_generic_deploy(rt: Runtime, payload: dict[str, Any]) -> dict[str, Any]:
    slug = str(payload.get("project") or "").strip().lower()
    deployment_id = str(payload.get("deployment_id") or "").strip()
    if not slug or not deployment_id:
        return {"ignored": "project and deployment_id are required"}
    if payload.get("environment", "production") != "production":
        return {"ignored": "not production"}
    with connect(rt.db_url) as conn:
        projects = list_active_projects(conn)
    from contracts.project import Project

    match = next((Project.from_row(r) for r in projects if r["slug"] == slug), None)
    if match is None:
        return {"ignored": f"no active project with slug {slug!r}"}
    changed = [u for u in (payload.get("changed_urls") or []) if isinstance(u, str)][:200]
    handle = run_workflow(rt, match, "post_deploy_audit", "deploy_webhook", f"deploy:{deployment_id}", {"changed_urls": changed})
    return {"project": match.slug, "run_id": str(handle.id), "status": handle.status, "created": handle.created}


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
