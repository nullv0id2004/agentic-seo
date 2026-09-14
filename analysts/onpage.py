"""onpage_analyst. Reads raw_crawl_pages, keywords, brand_rules. Emits title, meta and structure
suggestions. Suggestions only. Never edits.

Candidate pages are chosen in code (missing or duplicate titles, missing meta, length problems,
pages with a mapped keyword absent from the title). The model writes the suggestion text; brand
rules are shown to it as a courtesy and enforced by the gate regardless.
"""
from __future__ import annotations

import json
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from analysts.base import AnalystContext, AnalystInput
from contracts.artifacts import OnPageReport, OnPageSuggestion
from rules.indexability import rule_matches

NAME = "onpage"
MAX_PAGES = 40

SYSTEM = """You are the on-page SEO analyst for one web property. For each page you receive the current
title, meta description, h1, the primary keyword mapped to it and why it was selected. Propose
replacement text for the fields listed under needs. Titles 50 to 60 characters, meta descriptions
120 to 155 characters, one h1. Keep the brand voice: plain, specific, no hype. These phrases are
banned and will be rejected by code: {banned}. Never use an em dash. Say job seekers, never candidates.
Do not propose links to any url that is not in the page list."""


class _Suggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    field: str
    suggested: str
    rationale: str


class _Suggestions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestions: list[_Suggestion] = Field(default_factory=list)


def candidates(inp: AnalystInput) -> list[dict]:
    pages = [p for p in inp.table("raw_crawl_pages") if p.get("status_code") == 200 and p.get("title") is not None or p.get("status_code") == 200]
    protected = [r["url_pattern"] for r in inp.table("critical_rules")] if inp.rows.get("critical_rules") else []
    kw_by_url = {k["mapped_url"]: k["keyword"] for k in inp.table("keywords") if k.get("mapped_url") and not k.get("blocked_for_index")}
    titles = Counter(p.get("title") for p in pages if p.get("title"))
    out = []
    for p in pages:
        if any(rule_matches(pt, p["url"]) for pt in protected):
            continue
        needs = []
        t, m, h1 = p.get("title") or "", p.get("meta_description") or "", p.get("h1") or []
        kw = kw_by_url.get(p["url"])
        if not t or len(t) > 65 or len(t) < 25 or titles[t] > 1 or (kw and kw.lower() not in t.lower()):
            needs.append("title")
        if not m or len(m) > 160 or len(m) < 70:
            needs.append("meta_description")
        if len(h1) != 1:
            needs.append("h1")
        if needs:
            out.append({"id": p["id"], "url": p["url"], "title": t, "meta_description": m, "h1": h1, "primary_keyword": kw, "needs": needs})
    return out[:MAX_PAGES]


def run(ctx: AnalystContext, inp: AnalystInput) -> OnPageReport:
    cands = candidates(inp)
    if not cands:
        return OnPageReport(agent="onpage", suggestions=[])
    banned = [r["pattern"] for r in inp.table("brand_rules") if r.get("severity") == "block"]
    listing = [{k: v for k, v in c.items() if k != "id"} for c in cands]
    res = ctx.complete(agent=NAME, system=SYSTEM.format(banned=banned), user=json.dumps({"pages": listing}, indent=1), schema=_Suggestions, max_tokens=8192)
    by_url = {c["url"]: c for c in cands}
    fields = {"title", "meta_description", "h1", "structure", "internal_link"}
    out = []
    for s in res.suggestions:
        c = by_url.get(s.url)
        if not c or s.field not in fields:
            continue
        current = {"title": c["title"], "meta_description": c["meta_description"], "h1": " | ".join(c["h1"])}.get(s.field)
        out.append(OnPageSuggestion(evidence_ref=c["id"], url=s.url, field=s.field, current=current or None, suggested=s.suggested, rationale=s.rationale))
    return OnPageReport(agent="onpage", suggestions=out)
