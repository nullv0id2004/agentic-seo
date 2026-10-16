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
    relevant: bool | None = None   # the analyst's judgement against projects.description; only an explicit true is kept as an opportunity, false is never mapped
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


# ---------- linkbuilding ----------

LinkPhase = Literal["foundation", "growth", "authority"]


class PhaseSignal(Assertion):
    name: Literal["domain_rank", "site_age_years", "indexable_pages", "profiles_listed", "referring_domains"]
    value: float | None = None
    points_to: LinkPhase | None = None      # None: shown for context, not counted
    detail: str


class TacticOut(Assertion):
    slug: str
    name: str
    why: str
    first_action: str
    effort: str          # playbook guidance, not a measurement
    typical: str         # playbook guidance, not a measurement


class ChecklistItem(Assertion):
    kind: Literal["profile", "directory", "review"]
    platform: str
    url: str             # https://<domain>/
    status: Literal["todo", "done"]
    in_same_as: bool
    links_to_you: bool
    priority: int = 100
    action: str


class Prospect(Assertion):
    url: str             # https://<domain>/ of a site linking to competitors but not to the project
    category: Literal["directory", "resource_page", "blog", "news", "forum", "other"] | None = None
    competitors: int
    competitor_urls: list[str]
    domain_rank: int | None = None
    spam_score: int | None = None
    approach: str | None = None
    linking_url: str | None = None       # a page on the site that links to a competitor (raw_link_pages)
    evidence: str | None = None          # that page's title, quoted as collected


class AnchorShare(Assertion):
    bucket: Literal["branded", "naked_url", "generic", "partial_match", "exact_match", "other"]
    backlinks: int
    share_pct: float
    safe_range: str | None = None
    within: bool | None = None           # None: too few links to judge, or no range for this bucket


class MonthCount(Strict):
    month: str           # YYYY-MM
    new_domains: int


class Velocity(Assertion):
    months: list[MonthCount]
    program_month: int
    guideline: str
    last_month_new: int


class LinkFlag(Assertion):
    kind: Literal["velocity_spike", "exact_match_anchors", "spammy_links"]
    detail: str


class CompetitorAuthority(Assertion):
    url: str
    domain_rank: int | None = None
    referring_domains: int | None = None
    tier: Literal["realistic", "aspirational"] | None = None


class ProfileCopy(Assertion):
    name: str
    short: str | None = None
    medium: str | None = None
    long: str | None = None


class LinkPlanReport(Strict):
    agent: Literal["linkbuilding"] = "linkbuilding"
    phase: LinkPhase | None = None
    signals: list[PhaseSignal] = Field(default_factory=list)
    tactics: list[TacticOut] = Field(default_factory=list)
    checklist: list[ChecklistItem] = Field(default_factory=list)
    prospects: list[Prospect] = Field(default_factory=list)
    anchors: list[AnchorShare] = Field(default_factory=list)
    velocity: Velocity | None = None
    flags: list[LinkFlag] = Field(default_factory=list)
    competitors: list[CompetitorAuthority] = Field(default_factory=list)
    profile_copy: ProfileCopy | None = None
    prospects_skipped: int = 0           # gap candidates the analyst left out as not real opportunities


# ---------- trend ----------

class TrendEvent(Assertion):
    kind: Literal["algorithm_update", "serp_feature_change", "ai_overview_change", "ranking_shift", "ai_citation_change"]
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
    derived_from_rows: int = 0
    prior_value: float | None = None


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
    TechnicalReport | KeywordReport | OnPageReport | ContentBriefOut | OffPageReport | TrendReport | ReportOut | LinkPlanReport,
    Field(discriminator="agent"),
]

ARTIFACT_MODELS: dict[str, type[BaseModel]] = {
    "technical": TechnicalReport, "ecommerce": TechnicalReport, "keyword": KeywordReport, "onpage": OnPageReport,
    "content": ContentBriefOut, "offpage": OffPageReport, "trend": TrendReport, "report": ReportOut,
    "linkbuilding": LinkPlanReport,
}
