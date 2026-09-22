"""technical_analyst. Severity is computed in code before the model runs; the model writes only the
explanation and the claude_code_prompt for each issue it is handed.

Reads: raw_crawl_pages, raw_vitals, raw_sitemap_urls, critical_rules, projects.allowed_schema_types.
Emits: TechnicalReport (issues).
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from analysts.base import AnalystContext, AnalystInput
from contracts.artifacts import IssueOut, TechnicalReport
from rules.indexability import evaluate_critical_rules, rule_matches

NAME = "technical"
READS = ("raw_crawl_pages", "raw_vitals", "raw_sitemap_urls", "critical_rules")

LCP_MS = 2500
INP_MS = 200
CLS = 0.1
THIN_WORDS = 100

# Nested nodes that an allowed top-level type is made of. The crawler records every @type in a page's
# JSON-LD, nested ones included, so FAQPage must bring Question and Answer with it. Deterministic rule,
# not a prompt: a type is disallowed only if neither it nor any allowed parent covers it.
SCHEMA_CHILDREN: dict[str, frozenset[str]] = {
    "FAQPage": frozenset({"Question", "Answer"}),
    "BreadcrumbList": frozenset({"ListItem"}),
    "ItemList": frozenset({"ListItem"}),
    "Article": frozenset({"Person", "Organization", "ImageObject", "WebPage"}),
    "BlogPosting": frozenset({"Person", "Organization", "ImageObject", "WebPage"}),
    "WebSite": frozenset({"SearchAction", "EntryPoint", "Organization"}),
    "WebPage": frozenset({"Organization", "Person", "ImageObject"}),
    "Organization": frozenset({"PostalAddress", "ContactPoint", "ImageObject"}),
    "LocalBusiness": frozenset({"PostalAddress", "ContactPoint", "ImageObject", "GeoCoordinates", "OpeningHoursSpecification"}),
    "Product": frozenset({"Offer", "AggregateOffer", "AggregateRating", "Review", "Rating", "Brand", "ImageObject", "Person", "Organization"}),
}


def allowed_schema_types(declared: list[str] | None) -> set[str]:
    allowed = set(declared or [])
    for t in list(allowed):
        allowed |= SCHEMA_CHILDREN.get(t, frozenset())
    return allowed


@dataclass
class Detected:
    issue_type: str
    severity: str
    url: str | None
    evidence: str
    evidence_ref: Any


class _Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    recommended_fix: str
    claude_code_prompt: str | None = None


class _Explanations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explanations: list[_Explanation] = Field(default_factory=list)


SYSTEM = """You are the technical SEO analyst for one web property. You receive a list of issues that
deterministic code has already detected and classified. For each one write:
- recommended_fix: one to three sentences a developer can act on
- claude_code_prompt: a self-contained prompt a coding agent could execute to fix it in a Next.js codebase, or null if it is a content/ops task

Do not change the issue list, its order, its severity or its URLs. Do not add issues. Do not invent
numbers; cite only the figures given in the evidence. Never use an em dash. Refer to people who apply
for jobs as job seekers, never candidates."""


def detect(inp: AnalystInput) -> list[Detected]:
    """Deterministic pre-pass. Every Detected carries the raw row id it rests on."""
    project = inp.project
    pages = inp.table("raw_crawl_pages")
    vitals = inp.table("raw_vitals")
    rules = inp.table("critical_rules")
    sitemap = {r["url"] for r in inp.table("raw_sitemap_urls")}
    by_url = {p["url"]: p for p in pages}
    out: list[Detected] = []
    protected = [r for r in rules if r["assertion"] in ("must_noindex", "must_not_appear_in_sitemap")]

    def redact(url: str | None) -> str | None:
        # A protected path never appears in an artifact (gate stage 1 would block it). Cite the row instead.
        if url and any(rule_matches(r["url_pattern"], url) for r in protected):
            return None
        return url

    # critical: critical rule violations
    for v in evaluate_critical_rules(rules, pages, sitemap):
        row = by_url.get(v.url)
        out.append(Detected("critical_rule_violation", "critical", redact(v.url),
                            f"rule {v.rule_key} ({v.assertion}) violated: {v.detail}", row["id"] if row else None))
    # critical: disallowed JSON-LD types; schema contradicting content
    allowed = allowed_schema_types(project.allowed_schema_types)
    for p in pages:
        for t in p.get("schema_types") or []:
            if allowed and t not in allowed:
                out.append(Detected("disallowed_schema_type", "critical", redact(p["url"]),
                                    f"JSON-LD type {t} is not in the allowed list {sorted(project.allowed_schema_types or [])}", p["id"]))
    # high: vitals
    for v in vitals:
        if v.get("lcp_ms") is not None and v["lcp_ms"] > LCP_MS:
            out.append(Detected("lcp_slow", "high", v["url"], f"LCP {v['lcp_ms']:.0f}ms ({v['strategy']}, {v['source']}) above {LCP_MS}ms", v["id"]))
        if v.get("inp_ms") is not None and v["inp_ms"] > INP_MS:
            out.append(Detected("inp_slow", "high", v["url"], f"INP {v['inp_ms']:.0f}ms ({v['strategy']}, {v['source']}) above {INP_MS}ms", v["id"]))
        if v.get("cls") is not None and v["cls"] > CLS:
            out.append(Detected("cls_high", "high", v["url"], f"CLS {v['cls']:.3f} ({v['strategy']}, {v['source']}) above {CLS}", v["id"]))
    # high: canonical loops
    for p in pages:
        c = p.get("canonical")
        if c and c != p["url"] and c in by_url and by_url[c].get("canonical") == p["url"]:
            out.append(Detected("canonical_loop", "high", redact(p["url"]), f"canonical points to {c} which canonicals back", p["id"]))
    # high: orphaned pillar pages (in sitemap, 200, no inbound internal link in this crawl)
    inbound: Counter[str] = Counter()
    for p in pages:
        for link in p.get("internal_links") or []:
            inbound[link] += 1
    for p in pages:
        if p.get("in_sitemap") and p.get("status_code") == 200 and inbound[p["url"]] == 0 and _depth(p["url"]) <= 1 and p["url"].rstrip("/") != f"https://{project.primary_domain}":
            out.append(Detected("orphaned_pillar_page", "high", redact(p["url"]), "listed in sitemap but no internal link points to it", p["id"]))
    # medium: redirect chains, broken internal links, duplicate titles
    for p in pages:
        sc = p.get("status_code") or 0
        if 300 <= sc < 400 and p.get("canonical") in by_url and 300 <= (by_url[p["canonical"]].get("status_code") or 0) < 400:
            out.append(Detected("redirect_chain", "medium", redact(p["url"]), f"redirects to {p['canonical']} which redirects again", p["id"]))
        broken = [link for link in p.get("internal_links") or [] if link in by_url and (by_url[link].get("status_code") or 0) >= 400]
        if broken:
            out.append(Detected("broken_internal_links", "medium", redact(p["url"]), f"{len(broken)} internal link(s) return 4xx/5xx, first: {redact(broken[0]) or '[protected path]'}", p["id"]))
    titles: Counter[str] = Counter(p["title"] for p in pages if p.get("title") and p.get("status_code") == 200)
    for p in pages:
        if p.get("title") and titles[p["title"]] > 1:
            out.append(Detected("duplicate_title", "medium", redact(p["url"]), f"title {p['title']!r} shared by {titles[p['title']]} pages", p["id"]))
    # low: everything else
    for p in pages:
        if p.get("status_code") != 200 or p.get("robots_meta") and "noindex" in (p.get("robots_meta") or ""):
            if p.get("in_sitemap") and p.get("status_code") != 200:
                out.append(Detected("sitemap_url_not_200", "low", redact(p["url"]), f"sitemap url returned {p.get('status_code')}", p["id"]))
            continue
        if not p.get("title"):
            out.append(Detected("missing_title", "low", redact(p["url"]), "no <title>", p["id"]))
        if not p.get("meta_description"):
            out.append(Detected("missing_meta_description", "low", redact(p["url"]), "no meta description", p["id"]))
        h1s = p.get("h1") or []
        if len(h1s) == 0:
            out.append(Detected("missing_h1", "low", redact(p["url"]), "no h1", p["id"]))
        elif len(h1s) > 1:
            out.append(Detected("multiple_h1", "low", redact(p["url"]), f"{len(h1s)} h1 elements", p["id"]))
        if p.get("word_count") is not None and p["word_count"] < THIN_WORDS:
            out.append(Detected("thin_content", "low", redact(p["url"]), f"{p['word_count']} words", p["id"]))
        if not p.get("canonical"):
            out.append(Detected("missing_canonical", "low", redact(p["url"]), "no canonical link", p["id"]))
    return [d for d in out if d.evidence_ref is not None]


def _depth(url: str) -> int:
    return len([s for s in urlsplit(url).path.split("/") if s])


def run(ctx: AnalystContext, inp: AnalystInput) -> TechnicalReport:
    detected = detect(inp)
    if not detected:
        return TechnicalReport(agent="technical", issues=[])
    listing = [{"index": i, "issue_type": d.issue_type, "severity": d.severity, "url": d.url, "evidence": d.evidence}
               for i, d in enumerate(detected)]
    user = f"Project: {inp.project.display_name} ({inp.project.vertical}). Issues:\n{json.dumps(listing, indent=1)}"
    exp = ctx.complete(agent=NAME, system=SYSTEM, user=user, schema=_Explanations, max_tokens=8192)
    by_index = {e.index: e for e in exp.explanations}
    issues = []
    for i, d in enumerate(detected):
        e = by_index.get(i)
        issues.append(IssueOut(
            evidence_ref=d.evidence_ref, issue_type=d.issue_type, severity=d.severity, url=d.url, evidence=d.evidence,
            recommended_fix=(e.recommended_fix if e else f"Resolve {d.issue_type}: {d.evidence}"),
            claude_code_prompt=(e.claude_code_prompt if e else None),
        ))
    return TechnicalReport(agent="technical", issues=issues)
