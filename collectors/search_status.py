"""Google Search Status Dashboard: confirmed ranking updates only, each with its source url."""
from __future__ import annotations

from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client

FEED = "https://status.search.google.com/incidents.json"
DASHBOARD = "https://status.search.google.com/products/rGHU1fes5FTGOvANFw6k/history"


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    with client(transport=params.get("_transport"), follow_redirects=True) as http:
        ratelimit.acquire("search_status")
        r = http.get(FEED)
        if r.status_code != 200:
            ctx.gap(f"status feed returned {r.status_code}", "all")
            return
        try:
            incidents = r.json()
        except ValueError:
            ctx.gap("status feed was not json", "all")
            return
    n = 0
    for inc in incidents:
        name = inc.get("external_desc") or inc.get("title") or ""
        if "ranking" not in name.lower() and "update" not in name.lower():
            continue
        ctx.write("raw_search_status", {
            "update_name": name[:300], "status": inc.get("status"),
            "started_at": inc.get("begin"), "ended_at": inc.get("end"),
            "source_url": f"https://status.search.google.com/incidents/{inc.get('id')}" if inc.get("id") else DASHBOARD,
        })
        n += 1
        if n >= int(params.get("limit", 50)):
            break
