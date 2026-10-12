"""offpage_analyst. Reads mentions, raw_serp. Emits pitch drafts, hard capped per batch in code.

Outlets are chosen in code: domains that Google cites in AI Overviews or ranks in the top ten for a
tracked query and that do not already link to the property. Never the property's own registrable domain
(worldhire.com for korum.worldhire.com) and never a configured competitor: run 33a9a102 drafted pitches to
worldhire.com, LinkedIn, Glassdoor and foundit. The model writes subject and body; a draft that cites a
year before last year is dropped (it offered "trends in 2023" in 2026). Search results for a branded query
("kourm") list other companies with the same name, so those queries are skipped, as are sites that share the
brand name and sites that rank for everything (run c9c41cef pitched korum.in, korum.app, YouTube, Wikipedia).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from analysts.base import AnalystContext, AnalystInput
from config.settings import get_settings
from contracts.artifacts import OffPageReport, PitchOut
from rules.domains import GENERIC_DOMAINS, brand_label, host_of, registrable
from rules.keywords import brand_tokens, is_branded

NAME = "offpage"

SYSTEM = """You draft short outreach emails for one web property to outlets that already rank for the
topics it covers. Each pitch: a subject under 70 characters and a body under 120 words that offers one
specific, verifiable thing (data, a quote, an expert). No flattery, no hype, no em dashes, no promises
of links. Say job seekers, never candidates. Use only the facts given."""


class _Pitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outlet_domain: str
    subject: str
    body: str


class _Pitches(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pitches: list[_Pitch] = Field(default_factory=list)


def stale_year(text: str, this_year: int) -> bool:
    """True when the text names a year before last year: an offer of old data is not worth sending."""
    return any(1990 <= int(y) < this_year - 1 for y in re.findall(r"\b(19\d{2}|20\d{2})\b", text))


def run(ctx: AnalystContext, inp: AnalystInput) -> OffPageReport:
    cap = min(get_settings().pitch_batch_cap, int(inp.params.get("batch_cap", 50)))
    own = {registrable(d) for d in inp.project.domains}
    excluded = own | {registrable(c) for c in inp.project.competitors} | GENERIC_DOMAINS
    brand = brand_tokens(inp.project.display_name or inp.project.slug, list(inp.project.domains), inp.project.brand_context_terms)
    linking = {host_of(m["source_url"]) for m in inp.table("mentions") if m.get("kind") == "link"}

    def eligible(d: str) -> bool:
        return bool(d) and registrable(d) not in excluded and d not in linking and not is_branded(brand_label(d), brand)

    outlets: Counter[str] = Counter()
    evidence: dict[str, object] = {}
    queries: dict[str, set[str]] = {}
    for r in inp.table("raw_serp"):
        if is_branded(r.get("query") or "", brand):
            continue
        for c in r.get("ai_overview_citations") or []:
            d = host_of(c.get("domain") or c.get("url"))
            if eligible(d):
                outlets[d] += 2
                evidence.setdefault(d, r["id"])
                queries.setdefault(d, set()).add(r["query"])
        for x in r.get("results") or []:
            d = host_of(x.get("domain"))
            if eligible(d) and (x.get("rank") or 99) <= 10:
                outlets[d] += 1
                evidence.setdefault(d, r["id"])
                queries.setdefault(d, set()).add(r["query"])
    chosen = [d for d, _ in outlets.most_common(cap)]
    if not chosen:
        return OffPageReport(agent="offpage", pitches=[])
    listing = [{"outlet_domain": d, "ranks_for": sorted(queries[d])[:5]} for d in chosen]
    today = date.today()
    res = ctx.complete(agent=NAME, system=SYSTEM, user=json.dumps({"project": inp.project.display_name, "site": inp.project.primary_domain,
                                                                  "today": today.isoformat(), "outlets": listing}, indent=1),
                       schema=_Pitches, max_tokens=8192)
    by = {p.outlet_domain.lower(): p for p in res.pitches}
    out = []
    for d in chosen:
        p = by.get(d)
        if not p or stale_year(f"{p.subject} {p.body}", today.year):
            continue
        out.append(PitchOut(evidence_ref=evidence[d], outlet_url=f"https://{d}/", subject=p.subject, body=p.body))
    return OffPageReport(agent="offpage", pitches=out[:cap])   # the cap is code, not prompt
