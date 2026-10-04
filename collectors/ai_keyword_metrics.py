"""DataForSEO AI Keyword Data: how often a keyword is asked inside AI assistants (AI search volume).

Same keyword universe as keyword_metrics: the caller's list plus the project's keywords table.
"""
from __future__ import annotations

from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client
from config.settings import get_settings
from db.connection import jsonb

API = "https://api.dataforseo.com/v3/ai_optimization/ai_keyword_data/keywords_search_volume/live"
BATCH = 1000


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    s = get_settings()
    keywords: list[str] = list(params.get("keywords") or [])
    keywords += [r["keyword"] for r in ctx.read("select keyword from keywords where project_id = %(project_id)s order by keyword limit 700")]
    keywords = sorted({k.strip().lower() for k in keywords if k and k.strip()})
    if not keywords:
        ctx.gap("no keywords to fetch AI search volume for", "all")
        return
    if not (s.dataforseo_login and s.dataforseo_password) and not params.get("_transport"):
        ctx.gap("DATAFORSEO credentials not set", f"{len(keywords)} keywords")
        return
    location = int(params.get("ai_location_code", 2840))
    language = params.get("language_code", "en")
    auth = (s.dataforseo_login or "", s.dataforseo_password or "")
    with client(transport=params.get("_transport"), auth=auth) as http:
        for i in range(0, len(keywords), BATCH):
            batch = keywords[i:i + BATCH]
            ratelimit.acquire("dataforseo")
            r = http.post(API, json=[{"keywords": batch, "location_code": location, "language_code": language}])
            if r.status_code != 200:
                ctx.gap(f"ai keyword data returned {r.status_code}", f"{len(batch)} keywords")
                continue
            try:
                task = r.json()["tasks"][0]
                if task.get("status_code") != 20000:
                    ctx.gap(f"ai keyword data task status {task.get('status_code')}: {task.get('status_message')}", f"{len(batch)} keywords")
                    continue
                items = (task.get("result") or [{}])[0].get("items") or []
            except (KeyError, IndexError, TypeError):
                ctx.gap("ai keyword data response shape unexpected", f"{len(batch)} keywords")
                continue
            for it in items:
                ctx.write("raw_ai_keyword_metrics", {
                    "keyword": (it.get("keyword") or "").lower(), "location_code": location, "language_code": language,
                    "ai_search_volume": it.get("ai_search_volume"),
                    "monthly": jsonb([{"year": m.get("year"), "month": m.get("month"), "ai_search_volume": m.get("ai_search_volume")}
                                      for m in (it.get("ai_monthly_searches") or [])]),
                })
