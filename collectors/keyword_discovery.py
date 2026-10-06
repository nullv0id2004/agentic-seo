"""DataForSEO Labs keyword discovery. Quarterly, before keyword_metrics.

Two sources, both written to raw_keyword_ideas exactly as returned:
- ideas: keyword_ideas for the project's seeds and tracked keywords (source = 'idea')
- competitors: ranked_keywords for the project's configured competitors (projects.competitors)
  (source = 'competitor', with the competitor's rank and url). Without configured competitors this part
  is skipped with a gap row; the domains that most often outrank the site are logged as suggestions.

Competitor results are restricted, in the API request, to keywords containing one of the project's topic
words, so a large site such as a social network contributes keywords about this topic, not its whole
index. Topic words come from the seeds and tracked keywords; nothing here guesses intent.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client
from collectors.llm_responses import brand_tokens, is_branded
from config.settings import get_settings
from rules.domains import registrable

IDEAS_API = "https://api.dataforseo.com/v3/dataforseo_labs/google/keyword_ideas/live"
RANKED_API = "https://api.dataforseo.com/v3/dataforseo_labs/google/ranked_keywords/live"

IDEAS_LIMIT = 300
COMPETITOR_LIMIT = 200
COMPETITORS = 3
# Sites that rank for almost everything; their keyword lists say nothing about a competitor's strategy.
GENERIC_DOMAINS = frozenset({"youtube.com", "wikipedia.org", "en.wikipedia.org", "reddit.com", "quora.com", "facebook.com",
                             "instagram.com", "x.com", "twitter.com", "pinterest.com", "medium.com", "amazon.in", "amazon.com"})
# Words too broad to define a topic on their own.
GENERIC_WORDS = frozenset({"best", "top", "free", "online", "india", "near", "with", "from", "what", "how", "the", "for", "and",
                           "search", "platform", "platforms", "software", "website", "websites", "site", "sites", "services",
                           "service", "app", "apps", "list", "2024", "2025", "2026"})


def topic_words(keywords: list[str], brand: set[str], limit: int = 8) -> list[str]:
    """Most frequent non-generic words across the seeds and tracked keywords. Brand words and their
    misspellings ("kourm" for "korum", run be9ad355) are excluded."""
    counts: Counter[str] = Counter()
    for k in keywords:
        for w in re.findall(r"[a-z0-9]+", k.lower()):
            if len(w) >= 3 and w not in GENERIC_WORDS and not is_branded(w, brand):
                counts[w] += 1
    return [w for w, _ in counts.most_common(limit)]


def competitors_from_serp(rows: list[dict[str, Any]], own: set[str], limit: int = COMPETITORS) -> list[str]:
    """Domains appearing most often in the top 10 of the latest check of each tracked query."""
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        q = (r.get("query") or "").lower()
        if q not in latest or str(r.get("collected_at")) > str(latest[q].get("collected_at")):
            latest[q] = r
    counts: Counter[str] = Counter()
    for r in latest.values():
        seen: set[str] = set()
        for item in r.get("results") or []:
            d = registrable(item.get("domain"))
            rank = item.get("rank") or 99
            if not d or rank > 10 or d in seen or d in GENERIC_DOMAINS or d in own:
                continue
            seen.add(d)
            counts[d] += 1
    return [d for d, _ in counts.most_common(limit)]


def _task(ctx: CollectorContext, http, api: str, body: dict[str, Any], scope: str) -> dict[str, Any] | None:
    ratelimit.acquire("dataforseo")
    r = http.post(api, json=[body])
    if r.status_code != 200:
        ctx.gap(f"keyword discovery returned {r.status_code}", scope)
        return None
    try:
        task = r.json()["tasks"][0]
    except (KeyError, IndexError, TypeError, ValueError):
        ctx.gap("keyword discovery response shape unexpected", scope)
        return None
    if task.get("status_code") != 20000:
        ctx.gap(f"keyword discovery task status {task.get('status_code')}: {task.get('status_message')}", scope)
        return None
    return (task.get("result") or [None])[0] or {}


def _metrics(kd: dict[str, Any]) -> dict[str, Any]:
    info = kd.get("keyword_info") or {}
    props = kd.get("keyword_properties") or {}
    intent = (kd.get("search_intent_info") or {}).get("main_intent")
    return {"search_volume": info.get("search_volume"), "cpc": info.get("cpc"), "competition": info.get("competition"),
            "keyword_difficulty": props.get("keyword_difficulty", info.get("keyword_difficulty")), "intent": intent}


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    s = get_settings()
    project = ctx.project
    if not (s.dataforseo_login and s.dataforseo_password) and not params.get("_transport"):
        ctx.gap("DATAFORSEO credentials not set", "all")
        return
    location = int(params.get("location_code", 2356))   # India, as for keyword_metrics
    language = params.get("language_code", "en")
    tracked = [r["keyword"] for r in ctx.read("select keyword from keywords where project_id = %(project_id)s order by keyword limit 200")]
    seeds = sorted({k.strip().lower() for k in [*project.keyword_seeds, *(params.get("seeds") or []), *tracked] if k and k.strip()})
    own = {registrable(d) for d in project.domains}
    brand = brand_tokens(project.display_name or project.slug, list(project.domains), project.brand_context_terms)
    words = topic_words(seeds, brand)
    competitors = sorted({registrable(c) for c in (params.get("competitors") or project.competitors) if c})
    ctx.result.detail.update({"seeds": len(seeds), "topic_words": words, "competitors": competitors})
    if not competitors:
        suggested = competitors_from_serp(
            ctx.read("select query, results, collected_at from raw_serp where project_id = %(project_id)s order by collected_at desc limit 200"), own)
        ctx.result.detail["suggested_competitors"] = suggested
        ctx.gap(f"no competitors configured; suggestions from search results: {', '.join(suggested) or 'none'}", "competitors")
    auth = (s.dataforseo_login or "", s.dataforseo_password or "")
    with client(transport=params.get("_transport"), auth=auth, on_cost=ctx.add_cost) as http:
        if seeds:
            body = {"keywords": seeds[:200], "location_code": location, "language_code": language, "limit": int(params.get("ideas_limit", IDEAS_LIMIT)),
                    "filters": ["keyword_info.search_volume", ">", 0], "order_by": ["relevance,desc"]}
            result = _task(ctx, http, IDEAS_API, body, "ideas")
            for it in (result or {}).get("items") or []:
                kw = (it.get("keyword") or "").lower()
                if kw:
                    ctx.write("raw_keyword_ideas", {"keyword": kw, "source": "idea", "location_code": location, "language_code": language, **_metrics(it)})
        else:
            ctx.gap("no seeds or tracked keywords to expand", "ideas")
        if not words:
            ctx.gap("no topic words to filter competitor keywords by", "competitors")
            return
        topic = "|".join(re.escape(w) for w in words)
        for comp in competitors:
            body = {"target": comp, "location_code": location, "language_code": language, "limit": int(params.get("competitor_limit", COMPETITOR_LIMIT)),
                    "item_types": ["organic"], "filters": ["keyword_data.keyword", "regex", topic],
                    "order_by": ["keyword_data.keyword_info.search_volume,desc"]}
            result = _task(ctx, http, RANKED_API, body, comp)
            for it in (result or {}).get("items") or []:
                kd = it.get("keyword_data") or {}
                kw = (kd.get("keyword") or "").lower()
                el = it.get("ranked_serp_element") or {}
                serp = el.get("serp_item") or el
                if kw:
                    ctx.write("raw_keyword_ideas", {
                        "keyword": kw, "source": "competitor", "competitor": comp, "competitor_rank": serp.get("rank_absolute"),
                        "competitor_url": serp.get("url"), "location_code": location, "language_code": language, **_metrics(kd)})
