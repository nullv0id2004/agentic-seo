"""DataForSEO LLM Mentions: where the site and the brand appear in AI answers.

Two calls per target. target_metrics_lite gives the aggregate (mentions, AI search volume) per
platform; search_mentions gives the individual questions, answers and the sources the model relied
on. cites_project is computed here, deterministically, from the source domains.

Platform coverage is DataForSEO's: ChatGPT data exists for the United States and English only, so the
default location is 2840. Google AI Overview data follows the same location unless ai_location_code
is passed.
"""
from __future__ import annotations

from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client
from config.settings import get_settings
from db.connection import jsonb

METRICS_API = "https://api.dataforseo.com/v3/ai_optimization/llm_mentions/target_metrics_lite/live"
SEARCH_API = "https://api.dataforseo.com/v3/ai_optimization/llm_mentions/search_mentions/live"
MENTION_LIMIT = 100


def _domain_of(url: str | None) -> str:
    if not url:
        return ""
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    return host[4:] if host.startswith("www.") else host


def cites(sources: list[dict[str, Any]], domains: set[str]) -> bool:
    for src in sources:
        d = (src.get("domain") or _domain_of(src.get("url"))).lower()
        if d.startswith("www."):
            d = d[4:]
        if d in domains or any(d.endswith("." + x) for x in domains):
            return True
    return False


def _task(ctx: CollectorContext, http, api: str, body: dict[str, Any], scope: str) -> dict[str, Any] | None:
    ratelimit.acquire("dataforseo")
    r = http.post(api, json=[body])
    if r.status_code != 200:
        ctx.gap(f"llm mentions returned {r.status_code}", scope)
        return None
    try:
        task = r.json()["tasks"][0]
    except (KeyError, IndexError, TypeError):
        ctx.gap("llm mentions response shape unexpected", scope)
        return None
    if task.get("status_code") != 20000:
        ctx.gap(f"llm mentions task status {task.get('status_code')}: {task.get('status_message')}", scope)
        return None
    return (task.get("result") or [None])[0]


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    s = get_settings()
    project = ctx.project
    if not (s.dataforseo_login and s.dataforseo_password) and not params.get("_transport"):
        ctx.gap("DATAFORSEO credentials not set", "all")
        return
    location = int(params.get("ai_location_code", 2840))
    language = params.get("language_code", "en")
    domains = {d.lower().removeprefix("www.") for d in project.domains}
    brand = (project.display_name or project.slug).strip()
    targets = [
        ("domain", project.primary_domain, [{"domain": project.primary_domain.removeprefix("www."), "include_subdomains": True}]),
        ("brand", brand, [{"keyword": brand, "search_scope": ["answer", "brand_entities"]}]),
    ]
    auth = (s.dataforseo_login or "", s.dataforseo_password or "")
    with client(transport=params.get("_transport"), auth=auth) as http:
        for kind, label, target in targets:
            result = _task(ctx, http, METRICS_API, {"target": target, "location_code": location, "language_code": language}, f"metrics:{label}")
            for it in (result or {}).get("items") or []:
                m = it.get("metrics") or {}
                ctx.write("raw_llm_mention_metrics", {
                    "target": label, "target_kind": kind, "platform": it.get("platform") or "unknown",
                    "location_code": it.get("location") or location, "language_code": it.get("language") or language,
                    "mentions": m.get("mentions"), "ai_search_volume": m.get("ai_search_volume"),
                })
        body = {"target": [t for _, _, tt in targets for t in tt], "location_code": location, "language_code": language,
                "limit": int(params.get("mention_limit", MENTION_LIMIT)), "order_by": ["ai_search_volume,desc"]}
        result = _task(ctx, http, SEARCH_API, body, "search_mentions")
        for it in (result or {}).get("items") or []:
            sources = [{"rank": x.get("rank"), "domain": x.get("domain"), "url": x.get("url"), "title": x.get("title")}
                       for x in (it.get("sources") or [])]
            ctx.write("raw_llm_mentions", {
                "platform": it.get("platform") or "unknown", "model_name": it.get("model_name"),
                "location_code": it.get("location_code") or location, "language_code": it.get("language_code") or language,
                "question": it.get("question") or "", "answer": it.get("answer"), "sources": jsonb(sources),
                "cites_project": cites(sources, domains), "ai_search_volume": it.get("ai_search_volume"),
                "is_web_search_based": it.get("is_web_search_based"),
                "brand_entities": jsonb([{"rank": b.get("rank"), "title": b.get("title"), "category": b.get("category")} for b in (it.get("brand_entities") or [])]),
                "first_response_at": it.get("first_response_at"), "last_response_at": it.get("last_response_at"),
            })
