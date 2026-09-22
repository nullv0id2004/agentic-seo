"""report_analyst. Metrics are summed in code from raw rows. The model writes commentary only, and
the commentary may not contain a number that is not one of the metrics.

If a collection_gaps row exists for the period, the report names the gap in caveats and the affected
metrics are null. Nothing is interpolated. Search Console lags two to three days, so the cutoff date
is stated and never assumed to be today.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict

from analysts.base import AnalystContext, AnalystInput
from contracts.artifacts import Caveat, MetricOut, ReportOut

NAME = "report"

SYSTEM = """You write the commentary section of a monthly SEO report for one web property. You receive the
metrics that code has computed and the list of data gaps. Rules:
- Use only the numbers given in the metrics, written exactly as given. Do not compute new ones.
- Where a metric is null because of a gap, say the data is unavailable and why. Never estimate it.
- State the cutoff date. Be plain and brief: five to ten sentences. No em dashes, no headings.
- Refer to people who apply for jobs as job seekers, never candidates."""

NUM_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?%?")


class _Commentary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commentary: str


def _period(inp: AnalystInput) -> tuple[date, date, date, date]:
    params = inp.params
    end = date.fromisoformat(params["until"]) if params.get("until") else date.today() - timedelta(days=3)
    start = date.fromisoformat(params["since"]) if params.get("since") else (end.replace(day=1) if end.day > 1 else (end - timedelta(days=1)).replace(day=1))
    length = (end - start).days + 1
    return start, end, start - timedelta(days=length), start - timedelta(days=1)


def _totals_grain(rows: list[dict]) -> list[dict]:
    page_grain = [r for r in rows if r.get("query") is None]
    return page_grain or rows


def compute_metrics(inp: AnalystInput) -> tuple[list[MetricOut], list[Caveat], str, str]:
    start, end, pstart, pend = _period(inp)
    period = f"{start.isoformat()}..{end.isoformat()}"
    gaps = inp.table("collection_gaps")
    caveats = [Caveat(collector=g["collector"], reason=g["reason"], affected_scope=g.get("affected_scope")) for g in gaps]
    gapped = {g["collector"] for g in gaps}
    metrics: list[MetricOut] = []

    gsc = [r for r in inp.table("raw_gsc_performance") if r.get("source", "gsc") == "gsc"]
    # Totals come from the page grain (query is null), which Search Console reports completely; the
    # query grain omits anonymised queries. Older windows collected at query grain only fall back to it.
    cur = _totals_grain([r for r in gsc if start <= r["date"] <= end])
    prev = _totals_grain([r for r in gsc if pstart <= r["date"] <= pend])
    if "gsc_performance" in gapped or not cur:
        if "gsc_performance" not in gapped:
            caveats.append(Caveat(collector="gsc_performance", reason="no Search Console rows for the period", affected_scope=period))
        for name in ("gsc_clicks", "gsc_impressions"):
            ref = cur[0]["id"] if cur else (gsc[0]["id"] if gsc else None)
            if ref:
                metrics.append(MetricOut(evidence_ref=ref, name=name, value=None, period=period, derived_from_rows=0))
    else:
        for name, col in (("gsc_clicks", "clicks"), ("gsc_impressions", "impressions")):
            metrics.append(MetricOut(evidence_ref=cur[0]["id"], name=name, value=float(sum(r[col] or 0 for r in cur)), period=period,
                                     derived_from_rows=len(cur), prior_value=float(sum(r[col] or 0 for r in prev)) if prev else None))
        pos = [float(r["position"]) for r in cur if r.get("position") is not None]
        if pos:
            metrics.append(MetricOut(evidence_ref=cur[0]["id"], name="gsc_avg_position", value=round(sum(pos) / len(pos), 1), period=period, derived_from_rows=len(pos)))

    ga4 = [r for r in inp.table("raw_ga4_daily") if start <= r["date"] <= end]
    if ga4 and "ga4" not in gapped:
        organic = [r for r in ga4 if (r.get("channel") or "").lower().startswith("organic")]
        metrics.append(MetricOut(evidence_ref=ga4[0]["id"], name="ga4_organic_sessions", value=float(sum(r["sessions"] or 0 for r in organic)), period=period, derived_from_rows=len(organic)))
    elif "ga4" in gapped:
        ref = ga4[0]["id"] if ga4 else None
        if ref:
            metrics.append(MetricOut(evidence_ref=ref, name="ga4_organic_sessions", value=None, period=period))

    issues = inp.table("issues")
    if issues:
        sev = Counter(i["severity"] for i in issues)
        for s in ("critical", "high", "medium", "low"):
            metrics.append(MetricOut(evidence_ref=issues[0].get("evidence_ref"), name=f"open_issues_{s}", value=float(sev.get(s, 0)), period=period, derived_from_rows=len(issues)))

    serp = inp.table("raw_serp")
    if serp and "serp" not in gapped:
        domains = {d.lower() for d in inp.project.domains}
        cited = sum(1 for r in serp if any((c.get("domain") or "").lower() in domains for c in (r.get("ai_overview_citations") or [])))
        top10 = sum(1 for r in serp if any((x.get("domain") or "").lower() in domains and (x.get("rank") or 99) <= 10 for x in (r.get("results") or [])))
        metrics.append(MetricOut(evidence_ref=serp[0]["id"], name="serp_queries_tracked", value=float(len(serp)), period=period, derived_from_rows=len(serp)))
        metrics.append(MetricOut(evidence_ref=serp[0]["id"], name="serp_top10_queries", value=float(top10), period=period, derived_from_rows=len(serp)))
        metrics.append(MetricOut(evidence_ref=serp[0]["id"], name="ai_overview_citations", value=float(cited), period=period, derived_from_rows=len(serp)))

    mentions = [m for m in inp.table("mentions") if m.get("first_seen") and start <= m["first_seen"] <= end]
    if mentions:
        metrics.append(MetricOut(evidence_ref=None, name="new_mentions", value=float(len(mentions)), period=period, derived_from_rows=len(mentions)))

    metrics = [m for m in metrics if m.evidence_ref is not None]
    return metrics, caveats, period, end.isoformat()


def _allowed_numbers(metrics: list[MetricOut], cutoff: str, period: str) -> set[str]:
    allowed: set[str] = set()
    for m in metrics:
        for v in (m.value, m.prior_value):
            if v is None:
                continue
            allowed.update({f"{v:g}", f"{v:,.0f}", f"{v:.0f}", f"{v:.1f}", str(int(v)) if float(v).is_integer() else f"{v}"})
        allowed.add(str(m.derived_from_rows))
    for d in (cutoff, *period.split("..")):
        y, mo, da = d.split("-")
        allowed.update({d, y, mo, da, str(int(mo)), str(int(da))})
    return allowed


def foreign_numbers(text: str, allowed: set[str]) -> list[str]:
    out = []
    for m in NUM_RE.findall(text):
        token = m.rstrip("%")
        if token not in allowed and token.replace(",", "") not in allowed:
            out.append(m)
    return out


def run(ctx: AnalystContext, inp: AnalystInput) -> ReportOut:
    metrics, caveats, period, cutoff = compute_metrics(inp)
    payload = {
        "project": inp.project.display_name, "period": period, "cutoff_date": cutoff,
        "metrics": [m.model_dump(mode="json", exclude={"evidence_ref"}) for m in metrics],
        "gaps": [c.model_dump() for c in caveats],
    }
    allowed = _allowed_numbers(metrics, cutoff, period)
    user = json.dumps(payload, indent=1, default=str)
    last: list[str] = []
    for attempt in range(2):
        out = ctx.complete(agent=NAME, system=SYSTEM, user=user if attempt == 0 else user + f"\n\nYour previous commentary contained numbers not in the metrics: {last}. Remove them.", schema=_Commentary, max_tokens=1500)
        last = foreign_numbers(out.commentary, allowed)
        if not last:
            return ReportOut(agent="report", period=period, cutoff_date=cutoff, metrics=metrics, commentary=out.commentary, caveats=caveats)
    raise ValueError(f"report commentary introduced numbers not present in metrics: {last}")
