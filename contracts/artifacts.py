"""Analyst output contracts. Strict JSON, validated before anything else touches the output.

Every factual assertion carries evidence_ref: the id of the raw_* row it came from. The gate drops
assertions without one and blocks artifacts whose refs do not resolve.
"""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

IssueSeverity = Literal["critical", "high", "medium", "low"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Assertion(Strict):
    evidence_ref: UUID | None = Field(default=None, description="id of the raw_* row this assertion rests on")


# ---------- technical / ecommerce ----------

class IssueOut(Assertion):
    issue_type: str
    severity: IssueSeverity
    url: str | None = None
    evidence: str = Field(description="what the raw row shows, in one or two sentences")
    recommended_fix: str
    claude_code_prompt: str | None = None


class TechnicalReport(Strict):
    agent: Literal["technical", "ecommerce"] = "technical"
    issues: list[IssueOut]


# ---------- keyword ----------

class KeywordProposal(Assertion):
    keyword: str
    intent: Literal["informational", "navigational", "transactional", "commercial"] | None = None
    cluster: str | None = None
    mapped_url: str | None = None
    volume: int | None = None
    volume_is_range: bool = False
    volume_low: int | None = None
    volume_high: int | None = None
    blocked_for_index: bool = False
    rationale: str | None = None


class KeywordReport(Strict):
    agent: Literal["keyword"] = "keyword"
    keywords: list[KeywordProposal]


# ---------- onpage ----------

class OnPageSuggestion(Assertion):
    url: str
    field: Literal["title", "meta_description", "h1", "structure", "internal_link"]
    current: str | None = None
    suggested: str
    rationale: str


class OnPageReport(Strict):
    agent: Literal["onpage"] = "onpage"
    suggestions: list[OnPageSuggestion]


# ---------- content ----------

class SourceClaim(Strict):
    claim: str
    url: str
    fetched_doc_id: UUID
    verdict: Literal["verified", "wrong", "unverifiable"] | None = None
    supporting_span: str | None = None


class ContentBriefOut(Strict):
    agent: Literal["content"] = "content"
    keyword: str
    title: str
    answer_block: str
    outline: list[str]
    draft: str
    sources: list[SourceClaim]
    example_urls: list[str] = Field(default_factory=list)
    internal_links: list[str] = Field(default_factory=list)


# ---------- offpage ----------

class PitchOut(Assertion):
    outlet_url: str
    contact_hint: str | None = None
    subject: str
    body: str


class OffPageReport(Strict):
    agent: Literal["offpage"] = "offpage"
    pitches: list[PitchOut]


# ---------- trend ----------

class TrendEvent(Assertion):
    kind: Literal["algorithm_update", "serp_feature_change", "ai_overview_change", "ranking_shift"]
    name: str
    source_url: str
    observed_on: str
    detail: str


class TrendReport(Strict):
    agent: Literal["trend"] = "trend"
    events: list[TrendEvent]


# ---------- report ----------

class MetricOut(Assertion):
    name: str
    value: float | None
    period: str


class Caveat(Strict):
    collector: str
    reason: str
    affected_scope: str | None = None


class ReportOut(Strict):
    agent: Literal["report"] = "report"
    period: str
    cutoff_date: str
    metrics: list[MetricOut]
    commentary: str
    caveats: list[Caveat]


ArtifactModel = Annotated[
    TechnicalReport | KeywordReport | OnPageReport | ContentBriefOut | OffPageReport | TrendReport | ReportOut,
    Field(discriminator="agent"),
]

ARTIFACT_MODELS: dict[str, type[BaseModel]] = {
    "technical": TechnicalReport, "ecommerce": TechnicalReport, "keyword": KeywordReport, "onpage": OnPageReport,
    "content": ContentBriefOut, "offpage": OffPageReport, "trend": TrendReport, "report": ReportOut,
}
