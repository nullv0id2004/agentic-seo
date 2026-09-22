"""Search Console collectors: performance rows (daily) and URL inspection (weekly, capped)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.google_auth import GSC_SCOPE, access_token
from collectors.http import client

API = "https://searchconsole.googleapis.com/webmasters/v3"
INSPECT_API = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
ROW_LIMIT = 25000


def collect_performance(ctx: CollectorContext, params: dict[str, Any]) -> None:
    """Pull page-level totals and query x page rows for a date window. Search Console lags 2-3 days;
    callers pass the window, the collector never shifts it."""
    project = ctx.project
    if not project.gsc_property:
        ctx.gap("project has no gsc_property configured", "all")
        return
    end = date.fromisoformat(params["end_date"]) if params.get("end_date") else date.today() - timedelta(days=3)
    start = date.fromisoformat(params["start_date"]) if params.get("start_date") else end - timedelta(days=params.get("days", 1) - 1)
    token = access_token(project.credentials_ref, GSC_SCOPE)
    url = f"{API}/sites/{_enc(project.gsc_property)}/searchAnalytics/query"
    # Two grains, and no country or device dimension: Search Console drops any row that is too granular
    # to be anonymous, so every dimension added loses data on a small site (korum, 2026-09-19: date alone
    # 5 days, date+page 7 rows, date+page+country+device 1 row for the week). The page grain
    # (query = null) carries the complete totals; the query grain carries the keywords Google will show.
    grains = (("page", ["date", "page"]), ("query", ["date", "query", "page"]))
    written = {}
    with client(transport=params.get("_transport"), headers={"Authorization": f"Bearer {token}"}) as http:
        for grain, dims in grains:
            start_row = written[grain] = 0
            while True:
                ratelimit.acquire("gsc")
                body = {
                    "startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": dims,
                    "rowLimit": ROW_LIMIT, "startRow": start_row, "dataState": "final",
                }
                r = http.post(url, json=body)
                if r.status_code != 200:
                    ctx.gap(f"searchAnalytics.query ({grain} grain) returned {r.status_code}: {r.text[:200]}", f"{start}..{end} from row {start_row}")
                    return
                rows = r.json().get("rows", [])
                for row in rows:
                    keys = dict(zip(dims, row["keys"], strict=True))
                    ctx.write("raw_gsc_performance", {
                        "date": keys["date"], "query": keys.get("query"), "page": keys["page"], "country": keys.get("country"), "device": keys.get("device"),
                        "clicks": row.get("clicks"), "impressions": row.get("impressions"),
                        "ctr": row.get("ctr"), "position": row.get("position"), "source": "gsc",
                    })
                written[grain] += len(rows)
                if len(rows) < ROW_LIMIT:
                    break
                start_row += ROW_LIMIT
    ctx.result.detail.update({"start": start.isoformat(), "end": end.isoformat(), "page_rows": written["page"], "query_rows": written["query"]})


def collect_inspection(ctx: CollectorContext, params: dict[str, Any]) -> None:
    """URL Inspection for a list of urls. Writes raw_crawl_pages.indexable. Hard cap 2000/day per property."""
    from config.settings import get_settings

    project = ctx.project
    if not project.gsc_property:
        ctx.gap("project has no gsc_property configured", "all")
        return
    urls: list[str] = params.get("urls") or []
    if not urls:
        rows = ctx.read("select distinct url from raw_crawl_pages where project_id = %(project_id)s and run_id = %(run_id)s",
                        {"run_id": ctx.run_id})
        urls = [r["url"] for r in rows]
    cap = get_settings().gsc_inspection_daily_cap
    used = ctx.read("select count(*) as n from audit_log where project_id = %(project_id)s and event = 'gsc_inspection' and created_at::date = current_date")[0]["n"]
    remaining = max(0, cap - used)
    if remaining <= 0:
        ctx.gap("daily URL inspection cap reached", f"{len(urls)} urls not inspected")
        return
    if len(urls) > remaining:
        ctx.gap("daily URL inspection cap would be exceeded; inspecting a prefix only", f"{len(urls) - remaining} urls skipped")
        urls = urls[:remaining]
    token = access_token(project.credentials_ref, GSC_SCOPE)
    with client(transport=params.get("_transport"), headers={"Authorization": f"Bearer {token}"}) as http:
        for u in urls:
            ratelimit.acquire("gsc_inspection")
            r = http.post(INSPECT_API, json={"inspectionUrl": u, "siteUrl": project.gsc_property})
            ctx.scope.audit(f"collector:{ctx.name}", "gsc_inspection", {"url": u, "status": r.status_code}, run_id=ctx.run_id)
            if r.status_code != 200:
                ctx.gap(f"urlInspection returned {r.status_code}", u)
                continue
            res = r.json().get("inspectionResult", {}).get("indexStatusResult", {})
            verdict = res.get("verdict")
            indexable = None if verdict is None else (verdict == "PASS" and res.get("indexingState") == "INDEXING_ALLOWED")
            ctx.write("raw_crawl_pages", {
                "url": u, "indexable": indexable, "robots_meta": res.get("robotsTxtState"),
                "canonical": res.get("googleCanonical"),
            })


def _enc(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")
