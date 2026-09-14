"""DataForSEO SERP: organic results plus AI Overview presence and citations, per tracked query."""
from __future__ import annotations

from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client
from config.settings import get_settings
from db.connection import jsonb

API = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    s = get_settings()
    queries: list[str] = params.get("queries") or [
        r["keyword"] for r in ctx.read("select keyword from keywords where project_id = %(project_id)s and not blocked_for_index order by keyword limit 100")
    ]
    if not queries:
        ctx.gap("no tracked keywords for project", "all")
        return
    if not (s.dataforseo_login and s.dataforseo_password) and not params.get("_transport"):
        ctx.gap("DATAFORSEO credentials not set", f"{len(queries)} queries")
        return
    location = params.get("location_code", 2356)  # India
    auth = (s.dataforseo_login or "", s.dataforseo_password or "")
    with client(transport=params.get("_transport"), auth=auth) as http:
        for q in queries:
            ratelimit.acquire("dataforseo")
            r = http.post(API, json=[{"keyword": q, "location_code": location, "language_code": "en", "device": "desktop", "depth": 20}])
            if r.status_code != 200:
                ctx.gap(f"serp api returned {r.status_code}", q)
                continue
            data = r.json()
            try:
                task = data["tasks"][0]
                if task.get("status_code") != 20000:
                    ctx.gap(f"serp task status {task.get('status_code')}: {task.get('status_message')}", q)
                    continue
                items = task["result"][0].get("items") or []
            except (KeyError, IndexError, TypeError):
                ctx.gap("serp response shape unexpected", q)
                continue
            organic = [{"rank": it.get("rank_absolute"), "url": it.get("url"), "domain": it.get("domain"), "title": it.get("title")}
                       for it in items if it.get("type") == "organic"]
            ai = [it for it in items if it.get("type") == "ai_overview"]
            citations = []
            for it in ai:
                for ref in it.get("references") or []:
                    citations.append({"url": ref.get("url"), "domain": ref.get("domain"), "title": ref.get("title")})
                for sub in it.get("items") or []:
                    for ref in sub.get("references") or []:
                        citations.append({"url": ref.get("url"), "domain": ref.get("domain"), "title": ref.get("title")})
            ctx.write("raw_serp", {
                "query": q, "engine": "google", "location": str(location), "results": jsonb(organic),
                "ai_overview_present": bool(ai), "ai_overview_citations": jsonb(citations),
            })
