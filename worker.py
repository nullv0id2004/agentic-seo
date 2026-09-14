"""Worker entrypoint for Azure App Service.

Runs two things: an HTTP server for the Vercel deploy webhook and health checks, and a one-minute
scheduler loop. Everything else is driven from those two.

  python worker.py            serve (PORT env, default 8080)
  python worker.py tick       one scheduler tick, then exit (for an external cron)
  python worker.py run <slug> <workflow> [logical_date]
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
            if self.path != "/webhooks/vercel":
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            from orchestrator.webhooks import handle_vercel_deploy, verify_vercel_signature

            if not verify_vercel_signature(body, self.headers.get("x-vercel-signature"), os.environ.get("VERCEL_WEBHOOK_SECRET")):
                return self._json(401, {"error": "bad signature"})
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            threading.Thread(target=handle_vercel_deploy, args=(rt, payload), daemon=True).start()
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
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
