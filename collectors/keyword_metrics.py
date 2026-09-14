"""DataForSEO Labs / Google Ads search volume for tracked keywords plus seeds. Quarterly.

Writes exactly what the API returns. A range is stored as a range with volume_is_range = true and
volume null; nothing collapses a range into a point estimate.
"""
from __future__ import annotations

from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client
from config.settings import get_settings

API = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"
DIFFICULTY_API = "https://api.dataforseo.com/v3/dataforseo_labs/google/bulk_keyword_difficulty/live"


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    s = get_settings()
    keywords: list[str] = list(params.get("keywords") or [])
    keywords += [r["keyword"] for r in ctx.read("select keyword from keywords where project_id = %(project_id)s order by keyword limit 700")]
    keywords = sorted({k.strip().lower() for k in keywords if k.strip()})
    if not keywords:
        ctx.gap("no keywords to fetch metrics for", "all")
        return
    if not (s.dataforseo_login and s.dataforseo_password) and not params.get("_transport"):
        ctx.gap("DATAFORSEO credentials not set", f"{len(keywords)} keywords")
        return
    auth = (s.dataforseo_login or "", s.dataforseo_password or "")
    location = params.get("location_code", 2356)
    with client(transport=params.get("_transport"), auth=auth) as http:
        for i in range(0, len(keywords), 700):
            batch = keywords[i:i + 700]
            ratelimit.acquire("dataforseo")
            r = http.post(API, json=[{"keywords": batch, "location_code": location, "language_code": "en"}])
            if r.status_code != 200:
                ctx.gap(f"search_volume returned {r.status_code}", f"{len(batch)} keywords")
                continue
            try:
                task = r.json()["tasks"][0]
                if task.get("status_code") != 20000:
                    ctx.gap(f"search_volume task {task.get('status_code')}: {task.get('status_message')}", f"{len(batch)} keywords")
                    continue
                items = task.get("result") or []
            except (KeyError, IndexError, TypeError, ValueError):
                ctx.gap("search_volume response shape unexpected", f"{len(batch)} keywords")
                continue
            seen = set()
            for it in items:
                kw = (it.get("keyword") or "").lower()
                seen.add(kw)
                vol = it.get("search_volume")
                low = it.get("low_top_of_page_bid")   # a bid, not volume; used only as a cpc fallback
                is_range = isinstance(vol, dict)
                ctx.write("raw_keyword_metrics", {
                    "keyword": kw, "volume": None if is_range else vol, "volume_is_range": is_range,
                    "volume_low": vol.get("min") if is_range else None, "volume_high": vol.get("max") if is_range else None,
                    "difficulty": it.get("keyword_difficulty"), "cpc": it.get("cpc") or (float(low) if low else None),
                    "source": "dataforseo_google_ads",
                })
            for kw in batch:
                if kw not in seen:
                    ctx.gap("no metric returned for keyword", kw)
