"""content_analyst. Reads a brief (params), keywords, raw_fetched_documents for the cited sources,
raw_crawl_pages for internal link targets. Emits a ContentBriefOut.

Every statistic in the draft must map to a sources[] entry whose fetched document contains it. Code
enforces this after the model answers: a sentence carrying a number that no cited document supports is
removed from the draft before the gate ever sees it. The monthly content cap is enforced here in code
and is not a prompt.
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field

from analysts.base import UNTRUSTED_PREAMBLE, AnalystContext, AnalystInput, wrap_untrusted
from contracts.artifacts import ContentBriefOut, SourceClaim
from gate.stage2_verify import numerically_supported, typed_numbers_in
from rules.indexability import rule_matches

NAME = "content"


class ContentCapReached(RuntimeError):
    pass


SYSTEM = """You write a search-focused article brief and first draft for one web property. You receive the
target keyword, notes from the editor, internal urls you may link to, and the stored text of source
documents. Rules:
- Every statistic or figure in the draft must come from one of the documents and be listed in sources
  with the exact claim, the document url and its fetched_doc_id. Figures with no document are not allowed.
- Do not cite anything that is not in the documents. Do not add urls that are not in the internal list.
- answer_block: a direct 40 to 60 word answer to the search intent. draft: 600 to 900 words, plain prose.
- No em dashes. Say job seekers, never candidates. No hype words.
""" + UNTRUSTED_PREAMBLE


class _Src(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    fetched_doc_id: str


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    answer_block: str
    outline: list[str]
    draft: str
    sources: list[_Src] = Field(default_factory=list)
    internal_links: list[str] = Field(default_factory=list)


def run(ctx: AnalystContext, inp: AnalystInput) -> ContentBriefOut:
    cap = inp.project.monthly_content_cap
    this_month = [b for b in inp.table("content_briefs") if b.get("created_at") and b["created_at"].strftime("%Y-%m") == inp.params.get("month", b["created_at"].strftime("%Y-%m"))]
    if len(this_month) >= cap:
        raise ContentCapReached(f"{inp.project.slug}: {len(this_month)} briefs this month, cap is {cap} (Section 13.9)")
    keyword = inp.params.get("keyword") or next((k["keyword"] for k in inp.table("keywords")), None)
    if not keyword:
        raise ValueError("content brief needs a keyword")
    docs = {str(d["id"]): d for d in inp.table("raw_fetched_documents") if d.get("content_text")}
    if not docs:
        raise ValueError("no fetched documents to write from; run doc_fetch first")
    protected = [r["url_pattern"] for r in inp.table("critical_rules")] if inp.rows.get("critical_rules") else []
    internal = sorted({p["url"] for p in inp.table("raw_crawl_pages") if p.get("status_code") == 200 and not any(rule_matches(pt, p["url"]) for pt in protected)})
    docs_block = "\n\n".join(wrap_untrusted(d, docs[d].get("url", ""), docs[d]["content_text"][:15000]) for d in docs)
    user = (f"KEYWORD: {keyword}\nNOTES: {inp.params.get('notes', '')}\nINTERNAL URLS: {json.dumps(internal[:100])}\n\nDOCUMENTS:\n{docs_block}")
    res = ctx.complete(agent=NAME, system=SYSTEM, user=user, schema=_Draft, max_tokens=8192)

    # deterministic enforcement: sources must cite real documents and be numerically supported
    sources: list[SourceClaim] = []
    for s in res.sources:
        d = docs.get(s.fetched_doc_id)
        if not d:
            continue
        ok, _ = numerically_supported(s.claim, d["content_text"])
        if ok:
            sources.append(SourceClaim(claim=s.claim, url=d.get("url") or "", fetched_doc_id=d["id"]))
    supported_numbers = {(n, u) for s in sources for (n, u) in typed_numbers_in(s.claim)}
    draft = strip_unbacked_numbers(res.draft, supported_numbers)
    answer = strip_unbacked_numbers(res.answer_block, supported_numbers)
    return ContentBriefOut(agent="content", keyword=keyword, title=res.title, answer_block=answer, outline=res.outline, draft=draft,
                           sources=sources, example_urls=[], internal_links=[u for u in res.internal_links if u in internal])


def strip_unbacked_numbers(text: str, supported: set[tuple[float, str]]) -> str:
    """Remove every sentence that carries a number not present in a supported claim."""
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        nums = typed_numbers_in(sentence)
        if nums and not all(any(abs(n - sn) < 1e-9 and u == su for sn, su in supported) for n, u in nums):
            continue
        kept.append(sentence)
    return " ".join(kept).strip()
