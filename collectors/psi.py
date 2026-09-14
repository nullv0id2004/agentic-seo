"""PageSpeed Insights: lab and field vitals per url and strategy. Staggered across projects by the scheduler."""
from __future__ import annotations

from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client
from config.settings import get_settings

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    key = get_settings().pagespeed_api_key
    urls: list[str] = params.get("urls") or [f"https://{ctx.project.primary_domain}/"]
    strategies = params.get("strategies") or ["mobile", "desktop"]
    if not key and not params.get("_transport"):
        ctx.gap("PAGESPEED_API_KEY not set", f"{len(urls)} urls")
        return
    with client(transport=params.get("_transport")) as http:
        for u in urls:
            for strategy in strategies:
                ratelimit.acquire("psi")
                q = {"url": u, "strategy": strategy, "category": "performance"}
                if key:
                    q["key"] = key
                r = http.get(API, params=q, timeout=90)
                if r.status_code != 200:
                    ctx.gap(f"runPagespeed returned {r.status_code}", f"{u} {strategy}")
                    continue
                data = r.json()
                audits = data.get("lighthouseResult", {}).get("audits", {})
                lab = {
                    "lcp_ms": _num(audits.get("largest-contentful-paint", {}).get("numericValue")),
                    "inp_ms": _num(audits.get("interaction-to-next-paint", {}).get("numericValue")),
                    "cls": _num(audits.get("cumulative-layout-shift", {}).get("numericValue")),
                }
                ctx.write("raw_vitals", {"url": u, "strategy": strategy, "source": "lab", **lab})
                field = data.get("loadingExperience", {}).get("metrics")
                if field:
                    ctx.write("raw_vitals", {
                        "url": u, "strategy": strategy, "source": "field",
                        "lcp_ms": _num(field.get("LARGEST_CONTENTFUL_PAINT_MS", {}).get("percentile")),
                        "inp_ms": _num(field.get("INTERACTION_TO_NEXT_PAINT", {}).get("percentile")),
                        "cls": _cls(field.get("CUMULATIVE_LAYOUT_SHIFT_SCORE", {}).get("percentile")),
                    })
                else:
                    ctx.gap("no field data (CrUX) for url", f"{u} {strategy}")


def _num(v):
    return None if v is None else float(v)


def _cls(v):
    return None if v is None else float(v) / 100.0  # CrUX reports CLS percentile x100
