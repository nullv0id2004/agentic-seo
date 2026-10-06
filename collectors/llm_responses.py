"""DataForSEO LLM Responses: ask the assistants the questions a buyer would ask, with web search on,
and record whether the site is cited. Prompts are built deterministically from the tracked keywords
and the brand, so the same questions are asked every week and the answers are comparable.
"""
from __future__ import annotations

from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client
from collectors.llm_mentions import cites
from config.settings import get_settings
from db.connection import jsonb

API = "https://api.dataforseo.com/v3/ai_optimization/{platform}/llm_responses/live"
DEFAULT_MODELS = {"chat_gpt": "gpt-4.1", "gemini": "gemini-2.5-flash", "claude": "claude-sonnet-4-20250514", "perplexity": "sonar"}
PROMPT_TEMPLATES = ("What are the best options for {keyword} in India?", "Which websites should I use for {keyword}?")
MAX_PROMPTS = 12
# ChatGPT and Perplexity by default: the two assistants people use to research options with live sources.
# Each prompt costs about USD 0.07 on ChatGPT with search; pass platforms/max_prompts to change the spend.
DEFAULT_PLATFORMS = ("chat_gpt", "perplexity")


def request_body(platform: str, prompt: str, model: str, country: str) -> dict[str, Any]:
    """Fields differ per platform: ChatGPT needs web search switched on and forced; Perplexity's Sonar
    models always search and reject the web_search flag, taking only the country."""
    body: dict[str, Any] = {"user_prompt": prompt, "model_name": model, "max_output_tokens": 1024}
    if platform == "chat_gpt":
        # Without force, ChatGPT often answers from memory and cites nothing, so the prompt
        # cannot show whether the site would be cited.
        body.update({"web_search": True, "force_web_search": True, "web_search_country_iso_code": country})
    elif platform == "perplexity":
        body["web_search_country_iso_code"] = country
    else:
        body["web_search"] = True
    return body


def _edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance: an adjacent swap ("kourm" for "korum") counts as one edit."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def brand_tokens(brand: str, domains: list[str], context_terms: list[str]) -> set[str]:
    """Words that make a keyword navigational: the brand, the labels of the project's domains and the
    first context term (the parent brand). Generic labels such as www and com are left out."""
    toks = {brand.lower()} if brand else set()
    for d in domains:
        toks |= {x for x in d.lower().removeprefix("www.").split(".")[:-1] if len(x) > 2}
    if context_terms:
        toks.add(context_terms[0].lower())
    return {t for t in toks if t}


def is_branded(keyword: str, tokens: set[str]) -> bool:
    """True for a keyword that names the brand, including a one-edit misspelling with the same first
    letter ("kourm" for "korum", not "forum") and a split name ("world hire"). Asking an assistant for
    "the best options for kourm" measures nothing."""
    words = keyword.lower().split()
    candidates = words + [x + y for x, y in zip(words, words[1:], strict=False)]
    for word in candidates:
        for t in tokens:
            if word == t or (len(t) >= 4 and word[:1] == t[:1] and _edit_distance(word, t) <= 1):
                return True
    return False


def build_prompts(keywords: list[str], brand: str, limit: int = MAX_PROMPTS, qualifier: str | None = None,
                  tokens: set[str] | None = None) -> list[str]:
    named = f"{brand} ({qualifier})" if brand and qualifier else brand
    prompts = [f"What is {named} and what does it offer?"] if brand else []
    for kw in keywords:
        if tokens and is_branded(kw, tokens):
            continue
        for t in PROMPT_TEMPLATES:
            prompts.append(t.format(keyword=kw))
    seen: dict[str, None] = {}
    for p in prompts:
        seen.setdefault(p, None)
    return list(seen)[:limit]


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    s = get_settings()
    project = ctx.project
    if not (s.dataforseo_login and s.dataforseo_password) and not params.get("_transport"):
        ctx.gap("DATAFORSEO credentials not set", "all")
        return
    brand = project.display_name or project.slug
    keywords = params.get("keywords") or [r["keyword"] for r in ctx.read(
        "select keyword from keywords where project_id = %(project_id)s and not blocked_for_index order by (mapped_url is null), keyword limit 40")]
    qualifier = project.brand_context_terms[0] if project.brand_context_terms else None
    prompts = params.get("prompts") or build_prompts(keywords, brand, int(params.get("max_prompts", MAX_PROMPTS)), qualifier,
                                                     brand_tokens(brand, project.domains, project.brand_context_terms))
    if not prompts:
        ctx.gap("no prompts: the project has no tracked keywords yet", "all")
        return
    platforms = list(params.get("platforms") or DEFAULT_PLATFORMS)
    country = params.get("country_iso", "IN")
    domains = {d.lower().removeprefix("www.") for d in project.domains}
    auth = (s.dataforseo_login or "", s.dataforseo_password or "")
    with client(transport=params.get("_transport"), auth=auth, on_cost=ctx.add_cost) as http:
        for platform in platforms:
            model = params.get("models", {}).get(platform) or DEFAULT_MODELS.get(platform)
            if not model:
                ctx.gap(f"no default model for platform {platform}", platform)
                continue
            for prompt in prompts:
                ratelimit.acquire("dataforseo")
                body = request_body(platform, prompt, model, country)
                r = http.post(API.format(platform=platform), json=[body])
                if r.status_code != 200:
                    ctx.gap(f"llm responses ({platform}) returned {r.status_code}", prompt)
                    continue
                try:
                    task = r.json()["tasks"][0]
                    if task.get("status_code") != 20000:
                        ctx.gap(f"llm responses ({platform}) task status {task.get('status_code')}: {task.get('status_message')}", prompt)
                        continue
                    result = (task.get("result") or [{}])[0]
                except (KeyError, IndexError, TypeError):
                    ctx.gap(f"llm responses ({platform}) response shape unexpected", prompt)
                    continue
                text_parts: list[str] = []
                citations: list[dict[str, Any]] = []
                fan_out: list[str] = []
                for item in result.get("items") or []:
                    if item.get("type") != "message":
                        continue
                    for sec in item.get("sections") or []:
                        if sec.get("text"):
                            text_parts.append(sec["text"])
                        for a in sec.get("annotations") or []:
                            citations.append({"title": a.get("title"), "url": a.get("url"), "text": a.get("text")})
                    fan_out.extend(item.get("fan_out_queries") or [])
                ctx.write("raw_llm_responses", {
                    "platform": platform, "model_name": result.get("model_name") or model, "prompt": prompt,
                    "response": "\n\n".join(text_parts) or None, "citations": jsonb(citations), "fan_out_queries": jsonb(fan_out),
                    "cites_project": cites(citations, domains), "web_search": result.get("web_search"),
                    "input_tokens": result.get("input_tokens"), "output_tokens": result.get("output_tokens"),
                    "cost_usd": task.get("cost"),
                })
