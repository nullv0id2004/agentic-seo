"""trend_analyst. Reads raw_search_status, raw_serp (this run and the previous), mentions.

Events are detected in code: a confirmed update is a raw_search_status row with a source url; a SERP
change is a diff between two collected result sets for the same query. The model writes the detail
sentence for each event and nothing else. No speculation: an event without a raw row does not exist.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from analysts.base import AnalystContext, AnalystInput
from contracts.artifacts import TrendEvent, TrendReport

NAME = "trend"

SYSTEM = """You annotate SEO monitoring events that code has already detected for one web property.
For each event write one or two plain sentences of detail using only the facts given. Do not add
events, numbers, dates or causes that are not in the input. No em dashes. Say job seekers, not
candidates."""


@dataclass
class Detected:
    kind: str
    name: str
    source_url: str
    observed_on: str
    facts: str
    evidence_ref: Any


class _Detail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    detail: str


class _Details(BaseModel):
    model_config = ConfigDict(extra="forbid")
    details: list[_Detail] = Field(default_factory=list)


def detect(inp: AnalystInput) -> list[Detected]:
    out: list[Detected] = []
    for r in inp.table("raw_search_status"):
        if not r.get("source_url"):
            continue
        when = (r.get("started_at") or r.get("collected_at"))
        out.append(Detected("algorithm_update", r["update_name"], r["source_url"], _d(when),
                            f"status={r.get('status')} started={r.get('started_at')} ended={r.get('ended_at')}", r["id"]))
    serp = inp.table("raw_serp")
    by_query: dict[str, list[dict[str, Any]]] = {}
    for r in serp:
        by_query.setdefault(r["query"], []).append(r)
    domains = {d.lower() for d in inp.project.domains}
    for q, rows in by_query.items():
        rows.sort(key=lambda r: r["collected_at"])
        if len(rows) < 2:
            continue
        prev, cur = rows[-2], rows[-1]
        src = f"https://www.google.com/search?q={q.replace(' ', '+')}"
        if bool(prev.get("ai_overview_present")) != bool(cur.get("ai_overview_present")):
            out.append(Detected("ai_overview_change", f"AI Overview {'appeared' if cur.get('ai_overview_present') else 'disappeared'} for '{q}'", src,
                                _d(cur["collected_at"]), f"previous={prev.get('ai_overview_present')} current={cur.get('ai_overview_present')}", cur["id"]))
        pr = _rank(prev, domains)
        cr = _rank(cur, domains)
        if pr != cr and (pr is not None or cr is not None):
            out.append(Detected("ranking_shift", f"Rank for '{q}' moved {pr} -> {cr}", src, _d(cur["collected_at"]),
                                f"previous_rank={pr} current_rank={cr}", cur["id"]))
    return out


def _rank(row: dict[str, Any], domains: set[str]) -> int | None:
    for r in row.get("results") or []:
        if (r.get("domain") or "").lower() in domains:
            return r.get("rank")
    return None


def _d(value) -> str:
    if isinstance(value, date):
        return value.isoformat()[:10]
    return str(value)[:10]


def run(ctx: AnalystContext, inp: AnalystInput) -> TrendReport:
    detected = detect(inp)
    if not detected:
        return TrendReport(agent="trend", events=[])
    listing = [{"index": i, "kind": d.kind, "name": d.name, "observed_on": d.observed_on, "facts": d.facts} for i, d in enumerate(detected)]
    res = ctx.complete(agent=NAME, system=SYSTEM, user=json.dumps(listing, indent=1), schema=_Details, max_tokens=4096)
    by = {d.index: d.detail for d in res.details}
    events = [TrendEvent(evidence_ref=d.evidence_ref, kind=d.kind, name=d.name, source_url=d.source_url, observed_on=d.observed_on,
                         detail=by.get(i, d.facts)) for i, d in enumerate(detected)]
    return TrendReport(agent="trend", events=events)
