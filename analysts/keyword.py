"""keyword_analyst. Reads raw_keyword_metrics, raw_gsc_performance, keywords, critical_rules, raw_crawl_pages.

Hard rules, enforced in code after the model answers:
- volume fields are copied from the raw_keyword_metrics row; whatever the model wrote is discarded
- a metric row with volume_is_range keeps the range and the flag
- a keyword whose text or proposed url matches a protected pattern is flagged blocked_for_index and unmapped
- a keyword reserved by a cross-linked project (params["reserved_keywords"]) is never mapped here
- mapped_url must be a url this crawl saw, or null
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field

from analysts.base import AnalystContext, AnalystInput
from contracts.artifacts import KeywordProposal, KeywordReport
from rules.indexability import rule_matches

NAME = "keyword"

SYSTEM = """You are the keyword analyst for one web property. You receive what the project offers and to
whom, and for each keyword the search volume row, the Search Console pages that already receive impressions
for it, and the list of crawled urls. Assign: relevant (true only if the search itself shows the person is in
the audience the project serves and wants what it offers; false for generic searches anyone in the vertical
makes, such as any job, job alerts, government jobs, jobs near a place, work from home jobs, one employer's
careers page, a job title search ("sales executive jobs", "MIS executive jobs"), resume or CV templates and
formats, or what a word means, and false for another company or brand, a person, a different meaning of a word, or a market
the project does not serve; when unsure, false), intent (informational, navigational,
transactional, commercial), a short cluster label, and mapped_url (one of the crawled urls, or null if no
page fits). Do not invent volumes or urls. Do not use an em dash. Say job seekers, never candidates."""


class _Assign(BaseModel):
    # Extra keys (a model adding its own "rationale") are dropped rather than costing a validation retry.
    model_config = ConfigDict(extra="ignore")
    keyword: str
    relevant: bool | None = None
    intent: str | None = None
    cluster: str | None = None
    mapped_url: str | None = None


class _Assignments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignments: list[_Assign] = Field(default_factory=list)


def _same_url(a: str, b: str) -> bool:
    return a.rstrip("/").lower() == b.rstrip("/").lower()


def _protected_patterns(inp: AnalystInput) -> list[str]:
    return [r["url_pattern"] for r in inp.table("critical_rules") if r["assertion"] in ("must_noindex", "must_not_appear_in_sitemap")]


def _blocked_keyword(keyword: str, patterns: list[str]) -> bool:
    # a keyword that names a protected area (dashboard login, recruiter inbox) must never be targeted for indexing
    for p in patterns:
        names = re.findall(r"[a-z]{3,}", p)
        if any(re.search(rf"\b{n}\b", keyword, re.IGNORECASE) for n in names if n not in ("products",)):
            return True
    return False


def run(ctx: AnalystContext, inp: AnalystInput) -> KeywordReport:
    metrics = inp.table("raw_keyword_metrics")
    latest: dict[str, dict[str, Any]] = {}
    for m in metrics:
        latest[m["keyword"].lower()] = m
    if not latest:
        return KeywordReport(agent="keyword", keywords=[])
    patterns = _protected_patterns(inp)
    reserved = {k.lower() for k in inp.params.get("reserved_keywords", [])}
    # Only pages that can rank under their own URL: a page whose canonical names another URL (korum.worldhire.com/
    # canonical to /jobs) cannot hold a keyword. Inspection rows (no status code) are not pages.
    urls = sorted({p["url"] for p in inp.table("raw_crawl_pages")
                   if p.get("status_code") == 200 and not any(rule_matches(pt, p["url"]) for pt in patterns)
                   and (not p.get("canonical") or _same_url(urljoin(p["url"], p["canonical"]), p["url"]))})
    gsc_pages: dict[str, list[tuple[str, int]]] = {}
    for r in inp.table("raw_gsc_performance"):
        if r.get("query") and r.get("page"):
            gsc_pages.setdefault(r["query"].lower(), []).append((r["page"], r.get("impressions") or 0))
    ai_volume = {r["keyword"].lower(): r.get("ai_search_volume") for r in inp.table("raw_ai_keyword_metrics")}
    listing = []
    for kw, m in latest.items():
        top = sorted(gsc_pages.get(kw, []), key=lambda x: -x[1])[:3]
        listing.append({"keyword": m["keyword"], "volume": m.get("volume"), "volume_range": [m.get("volume_low"), m.get("volume_high")] if m.get("volume_is_range") else None,
                        "difficulty": m.get("difficulty"), "ai_search_volume": ai_volume.get(kw), "gsc_pages": [p for p, _ in top]})
    user = json.dumps({"project": inp.project.display_name, "offers": inp.project.description or f"{inp.project.vertical} website",
                       "crawled_urls": urls[:200], "keywords": listing}, indent=1)
    res = ctx.complete(agent=NAME, system=SYSTEM, user=user, schema=_Assignments, max_tokens=8192)
    by = {a.keyword.lower(): a for a in res.assignments}
    valid_intents = {"informational", "navigational", "transactional", "commercial"}
    out: list[KeywordProposal] = []
    for kw, m in latest.items():
        a = by.get(kw)
        blocked = _blocked_keyword(m["keyword"], patterns)
        mapped = a.mapped_url if a and a.mapped_url in urls else None
        if mapped and any(rule_matches(p, mapped) for p in patterns):
            blocked, mapped = True, None
        if kw in reserved:
            mapped = None
        relevant = a.relevant if a else None
        if relevant is False:
            mapped = None
        out.append(KeywordProposal(
            evidence_ref=m["id"], keyword=m["keyword"],
            intent=(a.intent if a and a.intent in valid_intents else None),
            cluster=(a.cluster if a else None), mapped_url=None if blocked else mapped,
            volume=None if m.get("volume_is_range") else m.get("volume"),
            volume_is_range=bool(m.get("volume_is_range")), volume_low=m.get("volume_low"), volume_high=m.get("volume_high"),
            blocked_for_index=blocked, relevant=relevant,
            # The model's free-text reasoning is not passed on: it is stored nowhere, and in run 4faf34b6 an em dash
            # in it made the brand gate block the whole report, mappings included.
            rationale=("reserved by a cross-linked project" if kw in reserved else None),
        ))
    return KeywordReport(agent="keyword", keywords=out)
