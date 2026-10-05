"""DataForSEO LLM Mentions: where the site and the brand appear in AI answers.

target_metrics_lite gives the domain's aggregate (mentions, AI search volume) per platform.
search_mentions, called once for the domain and once for the brand, gives the individual questions,
answers and the sources the model relied on. cites_project and about_project are computed here,
deterministically: a brand row is about the project only if it cites a project domain or names one of
the project's brand_context_terms, because other brands share the name.

Platform coverage is DataForSEO's: ChatGPT data exists for the United States and English only, so the
default location is 2840. Google AI Overview data follows the same location unless ai_location_code
is passed.
"""
from __future__ import annotations

import re
from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client
from config.settings import get_settings
from db.connection import jsonb

METRICS_API = "https://api.dataforseo.com/v3/ai_optimization/llm_mentions/target_metrics_lite/live"
SEARCH_API = "https://api.dataforseo.com/v3/ai_optimization/llm_mentions/search_mentions/live"
MENTION_LIMIT = 100
# The brand keyword matches every brand of the same name and DataForSEO bills per row returned. Run
# 7bdd20ae paid about USD 0.40 a week for 100 brand rows, none of them about this project.
BRAND_MENTION_LIMIT = 20


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


CONTEXT_WINDOW = 150   # characters either side of a brand mention in which a generic context term must appear


def about_project(text: str, sources: list[dict[str, Any]], domains: set[str], brand: str, context_terms: list[str]) -> bool:
    """A brand mention counts for this project when the answer cites one of its domains, names the brand
    with its qualifier (the first context term, e.g. WorldHire) anywhere, or names the brand with another
    context term within CONTEXT_WINDOW characters. Proximity matters: an answer about a denim brand that
    mentions "Korum Mall" in one paragraph and "job security" in another is not about this project.
    With no context terms the brand is taken as unambiguous."""
    if cites(sources, domains):
        return True
    low = (text or "").lower()
    hits = [m.start() for m in re.finditer(r"\b" + re.escape(brand.lower()) + r"\b", low)]
    if not hits:
        return False
    if not context_terms:
        return True
    qualifier, *generic = [t.lower() for t in context_terms]
    if re.search(r"\b" + re.escape(qualifier), low):
        return True
    for i in hits:
        window = low[max(0, i - CONTEXT_WINDOW): i + len(brand) + CONTEXT_WINDOW]
        if any(re.search(r"\b" + re.escape(t), window) for t in generic):
            return True
    return False


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
    domain_target = [{"domain": project.primary_domain.removeprefix("www."), "include_subdomains": True}]
    brand_target = [{"keyword": brand, "search_scope": ["answer", "brand_entities"]}]
    auth = (s.dataforseo_login or "", s.dataforseo_password or "")
    with client(transport=params.get("_transport"), auth=auth, on_cost=ctx.add_cost) as http:
        # Aggregates for the domain only. A brand keyword aggregate counts every brand of the same name
        # (KORUM the fishing tackle maker included), so brand visibility is counted row by row below.
        result = _task(ctx, http, METRICS_API, {"target": domain_target, "location_code": location, "language_code": language},
                       f"metrics:{project.primary_domain}")
        for it in (result or {}).get("items") or []:
            m = it.get("metrics") or {}
            ctx.write("raw_llm_mention_metrics", {
                "target": project.primary_domain, "target_kind": "domain", "platform": it.get("platform") or "unknown",
                "location_code": it.get("location") or location, "language_code": it.get("language") or language,
                "mentions": m.get("mentions"), "ai_search_volume": m.get("ai_search_volume"),
            })
        # One search per target: entities in one request are combined, so domain AND brand matched nothing.
        limits = {"domain": int(params.get("mention_limit", MENTION_LIMIT)),
                  "brand": int(params.get("brand_mention_limit", BRAND_MENTION_LIMIT))}
        for kind, target in (("domain", domain_target), ("brand", brand_target)):
            body = {"target": target, "location_code": location, "language_code": language,
                    "limit": limits[kind], "order_by": ["ai_search_volume,desc"]}
            result = _task(ctx, http, SEARCH_API, body, f"search_mentions:{kind}")
            for it in (result or {}).get("items") or []:
                sources = [{"rank": x.get("rank"), "domain": x.get("domain"), "url": x.get("url"), "title": x.get("title")}
                           for x in (it.get("sources") or [])]
                entities = [{"rank": b.get("rank"), "title": b.get("title"), "category": b.get("category")} for b in (it.get("brand_entities") or [])]
                text = " ".join([it.get("answer") or "", *(f"{b['title']} {b['category']}" for b in entities if b.get("title"))])
                ctx.write("raw_llm_mentions", {
                    "platform": it.get("platform") or "unknown", "model_name": it.get("model_name"),
                    "location_code": it.get("location_code") or location, "language_code": it.get("language_code") or language,
                    "question": it.get("question") or "", "answer": it.get("answer"), "sources": jsonb(sources),
                    "cites_project": cites(sources, domains), "ai_search_volume": it.get("ai_search_volume"),
                    "is_web_search_based": it.get("is_web_search_based"), "brand_entities": jsonb(entities),
                    "first_response_at": it.get("first_response_at"), "last_response_at": it.get("last_response_at"),
                    "target_kind": kind,
                    "about_project": True if kind == "domain" else about_project(text, sources, domains, brand, project.brand_context_terms),
                })
