"""ecommerce_analyst (RejuveLuxe only). Product and Offer schema validity, out-of-stock handling,
variant canonicalisation, and price consistency between page and schema.

Detection is code; the model explains. Feed comparison needs a Merchant Center feed collector that
does not exist yet, so price-vs-feed is reported as a gap, never guessed.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from analysts.base import AnalystContext, AnalystInput
from analysts.technical import SYSTEM as TECH_SYSTEM
from contracts.artifacts import IssueOut, TechnicalReport
from rules.indexability import evaluate_critical_rules, rule_matches

NAME = "ecommerce"
REQUIRED_PRODUCT = ("name", "offers")
REQUIRED_OFFER = ("price", "priceCurrency", "availability")


class _Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    recommended_fix: str
    claude_code_prompt: str | None = None


class _Explanations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explanations: list[_Explanation] = Field(default_factory=list)


def _nodes(jsonld: Any):
    if isinstance(jsonld, dict):
        yield jsonld
        for v in jsonld.values():
            yield from _nodes(v)
    elif isinstance(jsonld, list):
        for v in jsonld:
            yield from _nodes(v)


def detect(inp: AnalystInput) -> list[dict[str, Any]]:
    pages = inp.table("raw_crawl_pages")
    rules = inp.table("critical_rules")
    out: list[dict[str, Any]] = []
    product_pages = [p for p in pages if any(rule_matches(r["url_pattern"], p["url"]) for r in rules if r["assertion"] in ("schema_matches_visible", "must_canonical_to_parent"))
                     or any(n.get("@type") == "Product" for n in _nodes(p.get("raw_jsonld") or []))]
    for v in evaluate_critical_rules([r for r in rules if r["assertion"] in ("schema_matches_visible", "must_canonical_to_parent")], product_pages):
        page = next(p for p in product_pages if p["url"] == v.url)
        out.append({"issue_type": v.rule_key, "severity": "critical", "url": v.url, "evidence": v.detail, "evidence_ref": page["id"]})
    for p in product_pages:
        products = [n for n in _nodes(p.get("raw_jsonld") or []) if n.get("@type") == "Product"]
        if not products and p.get("status_code") == 200 and "/products/" in p["url"]:
            out.append({"issue_type": "product_schema_missing", "severity": "high", "url": p["url"], "evidence": "product page without Product JSON-LD", "evidence_ref": p["id"]})
        for prod in products:
            missing = [k for k in REQUIRED_PRODUCT if not prod.get(k)]
            if missing:
                out.append({"issue_type": "product_schema_incomplete", "severity": "critical", "url": p["url"], "evidence": f"Product missing {missing}", "evidence_ref": p["id"]})
                continue
            offers = prod["offers"] if isinstance(prod["offers"], list) else [prod["offers"]]
            for o in offers:
                if not isinstance(o, dict):
                    continue
                miss = [k for k in REQUIRED_OFFER if not o.get(k)]
                if miss:
                    out.append({"issue_type": "offer_schema_incomplete", "severity": "critical", "url": p["url"], "evidence": f"Offer missing {miss}", "evidence_ref": p["id"]})
                avail = str(o.get("availability") or "")
                if avail.endswith(("OutOfStock", "Discontinued")) and p.get("status_code") == 200 and not p.get("canonical"):
                    out.append({"issue_type": "out_of_stock_without_handling", "severity": "medium", "url": p["url"],
                                "evidence": f"availability {avail.rsplit('/', 1)[-1]} with no canonical or alternative signalled", "evidence_ref": p["id"]})
    return out


def run(ctx: AnalystContext, inp: AnalystInput) -> TechnicalReport:
    detected = detect(inp)
    if not detected:
        return TechnicalReport(agent="ecommerce", issues=[])
    listing = [{"index": i, **{k: v for k, v in d.items() if k != "evidence_ref"}} for i, d in enumerate(detected)]
    user = f"Project: {inp.project.display_name} (ecommerce). Issues:\n{json.dumps(listing, indent=1)}"
    exp = ctx.complete(agent=NAME, system=TECH_SYSTEM, user=user, schema=_Explanations, max_tokens=8192)
    by = {e.index: e for e in exp.explanations}
    issues = [IssueOut(evidence_ref=d["evidence_ref"], issue_type=d["issue_type"], severity=d["severity"], url=d["url"], evidence=d["evidence"],
                       recommended_fix=(by[i].recommended_fix if i in by else f"Resolve {d['issue_type']}: {d['evidence']}"),
                       claude_code_prompt=(by[i].claude_code_prompt if i in by else None)) for i, d in enumerate(detected)]
    return TechnicalReport(agent="ecommerce", issues=issues)
