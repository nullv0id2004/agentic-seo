"""DataForSEO Labs domain metrics for the project and each configured competitor. Monthly, or on demand.

- raw_domain_overview: estimated monthly organic traffic, ranking keywords and how they spread over positions, and
  how many keywords are new, up, down or lost since the last update
- raw_domain_history: the same by month, for the last twelve months (the trend)
- raw_domain_pages: each domain's top pages by estimated traffic

Google India in English, as keyword discovery. A target Labs has no data for (a new site) returns no items: nothing
is written for it and a gap row names it, so the console shows a dash, never a zero it was not given.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from collectors.base import CollectorContext
from collectors.dataforseo import as_int, as_num, live_result, months_back
from collectors.http import client
from config.settings import get_settings
from rules.domains import host_of, registrable

OVERVIEW_API = "https://api.dataforseo.com/v3/dataforseo_labs/google/domain_rank_overview/live"
HISTORY_API = "https://api.dataforseo.com/v3/dataforseo_labs/google/historical_rank_overview/live"
PAGES_API = "https://api.dataforseo.com/v3/dataforseo_labs/google/relevant_pages/live"
MAX_COMPETITORS = 12
TOP_PAGES = 10
MONTHS = 12
_DEEP = ("pos_21_30", "pos_31_40", "pos_41_50", "pos_51_60", "pos_61_70", "pos_71_80", "pos_81_90", "pos_91_100")


def organic(metrics: dict[str, Any] | None) -> dict[str, Any]:
    o = (metrics or {}).get("organic") or {}
    deep = [as_int(o.get(k)) for k in _DEEP]
    return {"etv": as_num(o.get("etv")), "keywords": as_int(o.get("count")), "pos_1": as_int(o.get("pos_1")),
            "pos_2_3": as_int(o.get("pos_2_3")), "pos_4_10": as_int(o.get("pos_4_10")), "pos_11_20": as_int(o.get("pos_11_20")),
            "pos_21_100": sum(d for d in deep if d is not None) if any(d is not None for d in deep) else None}


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    s = get_settings()
    if not (s.dataforseo_login and s.dataforseo_password) and not params.get("_transport"):
        ctx.gap("DATAFORSEO credentials not set", "all")
        return
    project = ctx.project
    own = host_of(project.primary_domain)
    competitors = sorted({registrable(c) for c in (params.get("competitors") or project.competitors) if c} - {registrable(own)})
    competitors = competitors[: int(params.get("max_competitors", MAX_COMPETITORS))]
    targets = [(own, "own")] + [(c, "competitor") for c in competitors]
    where = {"location_code": int(params.get("location_code", 2356)), "language_code": params.get("language_code", "en")}
    today = params.get("today") or date.today()
    auth = (s.dataforseo_login or "", s.dataforseo_password or "")
    found: dict[str, bool] = {}
    with client(transport=params.get("_transport"), auth=auth, on_cost=ctx.add_cost) as http:
        for target, role in targets:
            result = live_result(ctx, http, OVERVIEW_API, {"target": target, **where}, "domain overview", target)
            item = ((result or {}).get("items") or [None])[0]
            found[target] = bool(item)
            if result is not None and not item:
                ctx.gap("DataForSEO Labs has no organic data for this domain", target)
            if item:
                o = (item.get("metrics") or {}).get("organic") or {}
                ctx.write("raw_domain_overview", {
                    "target": target, "role": role, **organic(item.get("metrics")),
                    "is_new": as_int(o.get("is_new")), "is_up": as_int(o.get("is_up")), "is_down": as_int(o.get("is_down")),
                    "is_lost": as_int(o.get("is_lost")), "paid_etv": as_num(((item.get("metrics") or {}).get("paid") or {}).get("etv")),
                    "location_code": where["location_code"],
                })
            if not item:
                continue
            result = live_result(ctx, http, HISTORY_API, {"target": target, **where, "date_from": months_back(today, MONTHS).isoformat()},
                                 "domain history", target)
            for it in (result or {}).get("items") or []:
                y, m = as_int(it.get("year")), as_int(it.get("month"))
                if not y or not m or not 1 <= m <= 12:
                    continue
                o = organic(it.get("metrics"))
                ctx.write("raw_domain_history", {"target": target, "role": role, "month": date(y, m, 1), "etv": o["etv"], "keywords": o["keywords"],
                                                 "pos_1": o["pos_1"], "pos_2_3": o["pos_2_3"], "pos_4_10": o["pos_4_10"]})
            result = live_result(ctx, http, PAGES_API, {"target": target, **where, "limit": int(params.get("top_pages", TOP_PAGES)),
                                                        "order_by": ["metrics.organic.etv,desc"]}, "top pages", target)
            for it in (result or {}).get("items") or []:
                page = it.get("page_address")
                if not page:
                    continue
                o = organic(it.get("metrics"))
                ctx.write("raw_domain_pages", {"target": target, "role": role, "page": str(page)[:2000], "etv": o["etv"], "keywords": o["keywords"],
                                               "pos_1": o["pos_1"], "pos_2_3": o["pos_2_3"], "pos_4_10": o["pos_4_10"]})
    ctx.result.detail.update({"own": own, "competitors": competitors, "with_data": sorted(t for t, ok in found.items() if ok)})
