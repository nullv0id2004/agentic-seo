"""offpage_analyst. Reads mentions, raw_serp. Emits pitch drafts, hard capped per batch in code.

Outlets are chosen in code: domains that Google cites in AI Overviews or ranks in the top ten for a
tracked query and that do not already link to the property. The model writes subject and body.
"""
from __future__ import annotations

import json
from collections import Counter
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from analysts.base import AnalystContext, AnalystInput
from config.settings import get_settings
from contracts.artifacts import OffPageReport, PitchOut

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


def run(ctx: AnalystContext, inp: AnalystInput) -> OffPageReport:
    cap = min(get_settings().pitch_batch_cap, int(inp.params.get("batch_cap", 50)))
    own = {d.lower() for d in inp.project.domains}
    linking = {urlsplit(m["source_url"]).netloc.lower() for m in inp.table("mentions") if m.get("kind") == "link"}
    outlets: Counter[str] = Counter()
    evidence: dict[str, object] = {}
    queries: dict[str, set[str]] = {}
    for r in inp.table("raw_serp"):
        for c in r.get("ai_overview_citations") or []:
            d = (c.get("domain") or urlsplit(c.get("url") or "").netloc).lower()
            if d and d not in own and d not in linking:
                outlets[d] += 2
                evidence.setdefault(d, r["id"])
                queries.setdefault(d, set()).add(r["query"])
        for x in r.get("results") or []:
            d = (x.get("domain") or "").lower()
            if d and d not in own and d not in linking and (x.get("rank") or 99) <= 10:
                outlets[d] += 1
                evidence.setdefault(d, r["id"])
                queries.setdefault(d, set()).add(r["query"])
    chosen = [d for d, _ in outlets.most_common(cap)]
    if not chosen:
        return OffPageReport(agent="offpage", pitches=[])
    listing = [{"outlet_domain": d, "ranks_for": sorted(queries[d])[:5]} for d in chosen]
    res = ctx.complete(agent=NAME, system=SYSTEM, user=json.dumps({"project": inp.project.display_name, "site": inp.project.primary_domain, "outlets": listing}, indent=1),
                       schema=_Pitches, max_tokens=8192)
    by = {p.outlet_domain.lower(): p for p in res.pitches}
    out = []
    for d in chosen:
        p = by.get(d)
        if not p:
            continue
        out.append(PitchOut(evidence_ref=evidence[d], outlet_url=f"https://{d}/", subject=p.subject, body=p.body))
    return OffPageReport(agent="offpage", pitches=out[:cap])   # the cap is code, not prompt
