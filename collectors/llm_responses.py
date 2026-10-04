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


def build_prompts(keywords: list[str], brand: str, limit: int = MAX_PROMPTS) -> list[str]:
    prompts = [f"What is {brand} and what does it offer?"] if brand else []
    for kw in keywords:
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
    keywords = params.get("keywords") or [r["keyword"] for r in ctx.read(
        "select keyword from keywords where project_id = %(project_id)s and not blocked_for_index order by (mapped_url is null), keyword limit 6")]
    prompts = params.get("prompts") or build_prompts(keywords, project.display_name or project.slug, int(params.get("max_prompts", MAX_PROMPTS)))
    if not prompts:
        ctx.gap("no prompts: the project has no tracked keywords yet", "all")
        return
    platforms = list(params.get("platforms") or ["chat_gpt"])
    country = params.get("country_iso", "IN")
    domains = {d.lower().removeprefix("www.") for d in project.domains}
    auth = (s.dataforseo_login or "", s.dataforseo_password or "")
    with client(transport=params.get("_transport"), auth=auth) as http:
        for platform in platforms:
            model = params.get("models", {}).get(platform) or DEFAULT_MODELS.get(platform)
            if not model:
                ctx.gap(f"no default model for platform {platform}", platform)
                continue
            for prompt in prompts:
                ratelimit.acquire("dataforseo")
                body = {"user_prompt": prompt, "model_name": model, "web_search": True, "max_output_tokens": 1024}
                if platform == "chat_gpt":
                    body["web_search_country_iso_code"] = country
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
