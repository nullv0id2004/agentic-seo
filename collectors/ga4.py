"""GA4 Data API: organic sessions by landing page per day. Sibling dataset to Search Console."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.google_auth import GA4_SCOPE, access_token
from collectors.http import client

API = "https://analyticsdata.googleapis.com/v1beta"


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    project = ctx.project
    if not project.ga4_property_id:
        ctx.gap("project has no ga4_property_id configured", "all")
        return
    end = date.fromisoformat(params["end_date"]) if params.get("end_date") else date.today() - timedelta(days=1)
    start = date.fromisoformat(params["start_date"]) if params.get("start_date") else end - timedelta(days=params.get("days", 30) - 1)
    token = access_token(project.credentials_ref, GA4_SCOPE)
    body = {
        "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
        "dimensions": [{"name": "date"}, {"name": "landingPagePlusQueryString"}, {"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}, {"name": "engagedSessions"}, {"name": "conversions"}],
        "limit": 100000,
    }
    with client(transport=params.get("_transport"), headers={"Authorization": f"Bearer {token}"}) as http:
        ratelimit.acquire("ga4")
        r = http.post(f"{API}/properties/{project.ga4_property_id}:runReport", json=body)
        if r.status_code != 200:
            ctx.gap(f"runReport returned {r.status_code}: {r.text[:200]}", f"{start}..{end}")
            return
        for row in r.json().get("rows", []):
            d, path, channel = (v["value"] for v in row["dimensionValues"])
            sessions, engaged, conv = (v["value"] for v in row["metricValues"])
            ctx.write("raw_ga4_daily", {
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "page_path": path, "channel": channel,
                "sessions": int(sessions), "engaged_sessions": int(engaged), "conversions": float(conv),
            })
