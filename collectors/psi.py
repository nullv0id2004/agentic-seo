"""PageSpeed Insights: lab and field vitals per url and strategy. Staggered across projects by the scheduler.

Lab rows come from the Lighthouse run: performance score, LCP, FCP, TBT, CLS, server response time, and the audits
that would save the most time. Field rows come from CrUX, real Chrome users over 28 days. A page with too little
traffic of its own gets the origin's numbers instead; those are stored once per strategy against the origin
(field_scope 'origin'), never as the page's own, or ten measured pages would become ten copies of one number.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client
from config.settings import get_settings
from db.connection import jsonb

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
TOP_FIXES = 5
# Lighthouse audits that are measurements, not fixes
_METRIC_AUDITS = frozenset({"first-contentful-paint", "largest-contentful-paint", "total-blocking-time", "cumulative-layout-shift",
                            "speed-index", "interactive", "max-potential-fid", "interaction-to-next-paint", "server-response-time"})


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    key = get_settings().pagespeed_api_key
    urls: list[str] = params.get("urls") or [f"https://{ctx.project.primary_domain}/"]
    strategies = params.get("strategies") or ["mobile", "desktop"]
    if not key and not params.get("_transport"):
        ctx.gap("PAGESPEED_API_KEY not set", f"{len(urls)} urls")
        return
    origins_done: set[tuple[str, str]] = set()
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
                failed = (data.get("lighthouseResult") or {}).get("runtimeError")
                if failed:
                    ctx.gap(f"Lighthouse could not measure the page: {failed.get('code')}", f"{u} {strategy}")
                else:
                    ctx.write("raw_vitals", {"url": u, "strategy": strategy, **lab_row(data)})
                page = data.get("loadingExperience") or {}
                if page.get("metrics") and not page.get("origin_fallback"):
                    ctx.write("raw_vitals", {"url": u, "strategy": strategy, "field_scope": "url", **field_row(page)})
                else:
                    ctx.gap("no field data (CrUX) for url", f"{u} {strategy}")
                origin = data.get("originLoadingExperience") or (page if page.get("origin_fallback") else {})
                site = _origin(u)
                if origin.get("metrics") and (site, strategy) not in origins_done:
                    origins_done.add((site, strategy))
                    ctx.write("raw_vitals", {"url": site, "strategy": strategy, "field_scope": "origin", **field_row(origin)})


def lab_row(data: dict[str, Any]) -> dict[str, Any]:
    lh = data.get("lighthouseResult") or {}
    audits = lh.get("audits") or {}
    score = ((lh.get("categories") or {}).get("performance") or {}).get("score")
    return {
        "source": "lab",
        "performance_score": None if score is None else round(float(score) * 100),
        "lcp_ms": _audit(audits, "largest-contentful-paint"),
        "inp_ms": _audit(audits, "interaction-to-next-paint"),   # absent from a page-load run: nobody interacts
        "cls": _audit(audits, "cumulative-layout-shift"),
        "fcp_ms": _audit(audits, "first-contentful-paint"),
        "tbt_ms": _audit(audits, "total-blocking-time"),
        "ttfb_ms": _audit(audits, "server-response-time"),
        "opportunities": jsonb(top_fixes(audits)),
    }


def field_row(experience: dict[str, Any]) -> dict[str, Any]:
    m = experience.get("metrics") or {}

    def p(name: str):
        return _num((m.get(name) or {}).get("percentile"))

    return {
        "source": "field",
        "lcp_ms": p("LARGEST_CONTENTFUL_PAINT_MS"),
        "inp_ms": p("INTERACTION_TO_NEXT_PAINT"),
        "cls": _cls((m.get("CUMULATIVE_LAYOUT_SHIFT_SCORE") or {}).get("percentile")),
        "fcp_ms": p("FIRST_CONTENTFUL_PAINT_MS"),
        "ttfb_ms": p("EXPERIMENTAL_TIME_TO_FIRST_BYTE"),
        "field_category": experience.get("overall_category"),
    }


def top_fixes(audits: dict[str, Any], n: int = TOP_FIXES) -> list[dict[str, Any]]:
    """The failing audits with the largest estimated saving, largest first. Lighthouse states the saving either as
    details.overallSavingsMs or per metric in metricSavings; the larger of LCP, FCP and TBT is used. CLS savings are
    not time and do not rank."""
    out = []
    for aid, a in audits.items():
        if aid in _METRIC_AUDITS or a.get("score") is None or a["score"] >= 0.9:
            continue
        per_metric = a.get("metricSavings") or {}
        candidates = [(a.get("details") or {}).get("overallSavingsMs"), per_metric.get("LCP"), per_metric.get("FCP"), per_metric.get("TBT")]
        saving = max((float(v) for v in candidates if isinstance(v, (int, float))), default=0.0)
        if saving <= 0:
            continue
        out.append({"id": aid, "title": a.get("title"), "display_value": a.get("displayValue"), "savings_ms": round(saving)})
    out.sort(key=lambda f: (-f["savings_ms"], f["id"]))
    return out[:n]


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _audit(audits: dict[str, Any], name: str):
    return _num((audits.get(name) or {}).get("numericValue"))


def _num(v):
    return None if v is None else float(v)


def _cls(v):
    return None if v is None else float(v) / 100.0  # CrUX reports CLS percentile x100
