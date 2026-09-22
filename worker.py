"""Worker entrypoint for Azure App Service.

Runs two things: an HTTP server for the deploy webhooks (/webhooks/deploy for any platform with a
bearer secret, /webhooks/vercel for Vercel's signed hook) and health checks, and a one-minute
scheduler loop. Everything else is driven from those two.

  python worker.py            serve (PORT env, default 8080)
  python worker.py tick       one scheduler tick, then exit (for an external cron)
  python worker.py run <slug> <workflow> [logical_date]
  python worker.py check-google <slug>   resolve the project's Google credential and confirm the
                                         Search Console and GA4 grants, without writing anything
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from orchestrator.runtime import Runtime


def make_handler(rt: Runtime):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                return self._json(200, {"ok": True, "time": datetime.now(UTC).isoformat()})
            return self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            from orchestrator.webhooks import (
                handle_generic_deploy,
                handle_vercel_deploy,
                verify_bearer,
                verify_vercel_signature,
            )

            if self.path not in ("/webhooks/vercel", "/webhooks/deploy"):
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            if self.path == "/webhooks/vercel":
                if not verify_vercel_signature(body, self.headers.get("x-vercel-signature"), os.environ.get("VERCEL_WEBHOOK_SECRET")):
                    return self._json(401, {"error": "bad signature"})
                handler = handle_vercel_deploy
            else:
                if not verify_bearer(self.headers.get("Authorization"), os.environ.get("DEPLOY_WEBHOOK_SECRET")):
                    return self._json(401, {"error": "bad secret"})
                handler = handle_generic_deploy
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            threading.Thread(target=handler, args=(rt, payload), daemon=True).start()
            return self._json(202, {"accepted": True})

        def log_message(self, fmt, *args):  # quiet
            return

    return Handler


def scheduler_loop(rt: Runtime) -> None:
    from orchestrator.scheduler import tick

    while True:
        try:
            started = tick(rt)
            for s in started:
                print(f"[scheduler] {s}")
        except Exception as e:  # never let the loop die
            print(f"[scheduler] error: {type(e).__name__}: {e}")
        time.sleep(60 - datetime.now().second)


def serve(rt: Runtime) -> None:
    port = int(os.environ.get("PORT", "8080"))
    threading.Thread(target=scheduler_loop, args=(rt,), daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(rt))
    print(f"worker listening on :{port}")
    server.serve_forever()


def main(argv: list[str]) -> int:
    rt = Runtime()
    if not argv:
        serve(rt)
        return 0
    if argv[0] == "tick":
        from orchestrator.scheduler import tick
        print(json.dumps(tick(rt), default=str))
        return 0
    if argv[0] == "run" and len(argv) >= 3:
        from contracts.project import Project
        from db.connection import connect, list_active_projects
        from orchestrator.runner import run_workflow

        with connect(rt.db_url) as conn:
            rows = [r for r in list_active_projects(conn) if r["slug"] == argv[1]]
        if not rows:
            print(f"no active project {argv[1]!r}")
            return 2
        logical = argv[3] if len(argv) > 3 else datetime.now(UTC).date().isoformat()
        h = run_workflow(rt, Project.from_row(rows[0]), argv[2], "manual", logical)
        print(json.dumps({"run_id": str(h.id), "status": h.status, "created": h.created}))
        return 0
    if argv[0] == "check-google" and len(argv) == 2:
        return check_google(rt, argv[1])
    print(__doc__)
    return 1


def check_google(rt: Runtime, slug: str) -> int:
    """Diagnostic for onboarding: does the credential resolve, does it mint tokens, can it see the
    project's Search Console property and GA4 property? Read-only, writes no rows."""
    import httpx

    from collectors.google_auth import GA4_SCOPE, GSC_SCOPE, access_token, credentials_info
    from config.secrets import resolve_secret
    from contracts.project import Project
    from db.connection import connect, list_active_projects

    with connect(rt.db_url) as conn:
        rows = [r for r in list_active_projects(conn) if r["slug"] == slug]
    if not rows:
        print(f"no active project {slug!r}")
        return 2
    project = Project.from_row(rows[0])
    ok = True

    def report(step: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"[{'ok' if good else 'FAIL'}] {step}{': ' + detail if detail else ''}")

    try:
        info = credentials_info(resolve_secret(project.credentials_ref))
        who = info.get("client_email") or info.get("service_account_impersonation_url", "").rsplit("/", 1)[-1].replace(":generateAccessToken", "")
        report(f"secret {project.credentials_ref} resolves", True, f"type={info.get('type')} principal={who}")
    except Exception as e:
        report(f"secret {project.credentials_ref} resolves", False, f"{type(e).__name__}: {e}")
        return 1
    tokens = {}
    for scope in (GSC_SCOPE, GA4_SCOPE):
        try:
            tokens[scope] = access_token(project.credentials_ref, scope)
            report(f"token for {scope.rsplit('/', 1)[-1]}", True)
        except Exception as e:
            report(f"token for {scope.rsplit('/', 1)[-1]}", False, f"{type(e).__name__}: {e}")
    with httpx.Client(timeout=30) as http:
        if GSC_SCOPE in tokens:
            r = http.get("https://searchconsole.googleapis.com/webmasters/v3/sites", headers={"Authorization": f"Bearer {tokens[GSC_SCOPE]}"})
            sites = {s["siteUrl"]: s["permissionLevel"] for s in r.json().get("siteEntry", [])} if r.status_code == 200 else {}
            report("Search Console sites visible to the principal", r.status_code == 200, f"{r.status_code} {sites or r.text[:160]}")
            level = sites.get(project.gsc_property or "")
            report(f"property {project.gsc_property} granted", level is not None and level != "siteUnverifiedUser",
                   f"permission={level}" if level else "not in the list: add the principal as a user on this property")
            if level:
                from datetime import date, timedelta
                from urllib.parse import quote

                end, start = date.today() - timedelta(days=3), date.today() - timedelta(days=30)
                r = http.post(f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(project.gsc_property, safe='')}/searchAnalytics/query",
                              headers={"Authorization": f"Bearer {tokens[GSC_SCOPE]}"},
                              json={"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": ["date"], "rowLimit": 100})
                days = r.json().get("rows", []) if r.status_code == 200 else []
                report(f"Search Console data {start}..{end}", r.status_code == 200,
                       f"{len(days)} days with data, clicks={sum(int(d['clicks']) for d in days)}, impressions={sum(int(d['impressions']) for d in days)}"
                       if r.status_code == 200 else f"{r.status_code} {r.text[:160]}")
        if GA4_SCOPE in tokens:
            if not project.ga4_property_id:
                report("ga4_property_id set on the project row", False, "null: set it, then run this again")
            else:
                r = http.post(f"https://analyticsdata.googleapis.com/v1beta/properties/{project.ga4_property_id}:runReport",
                              headers={"Authorization": f"Bearer {tokens[GA4_SCOPE]}"},
                              json={"dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}], "metrics": [{"name": "sessions"}]})
                total = r.json().get("rows", [{}])[0].get("metricValues", [{}])[0].get("value") if r.status_code == 200 else None
                report(f"GA4 property {project.ga4_property_id} readable", r.status_code == 200,
                       f"sessions last 7 days={total or 0}" + ("" if total else " (no hits recorded: is the GA4 tag installed on the site?)")
                       if r.status_code == 200 else f"{r.status_code} {r.text[:160]}")
    print("all good" if ok else "something is not right; fix the FAIL lines above and run again")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
