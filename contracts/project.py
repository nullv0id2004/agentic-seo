from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

Vertical = Literal["recruitment", "ecommerce", "content", "saas"]
RuleType = Literal["banned_phrase", "required_phrase", "char_ban", "regex"]
Severity = Literal["block", "warn"]

FORBIDDEN_SLUGS = {"cruise-guru", "cruiseguru", "cruise_guru"}  # Section 13.10


class BrandRule(BaseModel):
    id: UUID | None = None
    rule_type: RuleType = Field(alias="type")
    pattern: str
    severity: Severity
    rationale: str | None = None
    active: bool = True

    model_config = {"populate_by_name": True}


class CriticalRule(BaseModel):
    id: UUID | None = None
    rule_key: str = Field(alias="key")
    url_pattern: str
    assertion: str
    active: bool = True

    model_config = {"populate_by_name": True}

    @field_validator("assertion")
    @classmethod
    def _known_assertion(cls, v: str) -> str:
        base = v.split(":", 1)[0]
        allowed = {"must_noindex", "must_index", "must_not_appear_in_sitemap", "schema_type_forbidden",
                   "schema_matches_visible", "must_canonical_to_parent"}
        if base not in allowed:
            raise ValueError(f"unknown assertion {v!r}")
        return v


class ProjectConfig(BaseModel):
    """What config/projects/<slug>.yaml declares. Seeds projects, brand_rules, critical_rules."""

    slug: str
    display_name: str | None = None
    vertical: Vertical
    domains: list[str]
    gsc_property: str | None = None
    ga4_property_id: str | None = None
    credentials_ref: str | None = None
    approver_id: UUID
    monthly_cost_cap_usd: float = 50
    monthly_content_cap: int = 4
    enabled_agents: list[str] = Field(default_factory=list)
    allowed_schema_types: list[str] = Field(default_factory=list)
    critical_rules: list[CriticalRule] = Field(default_factory=list)
    brand_rules: list[BrandRule] = Field(default_factory=list)
    critical_paths: list[str] = Field(default_factory=list)   # probed daily by header_probe
    keyword_seeds: list[str] = Field(default_factory=list)
    cross_link_exclusions: list[str] = Field(default_factory=list)  # slugs whose primary keywords this project may not claim

    @field_validator("slug")
    @classmethod
    def _not_cruise_guru(cls, v: str) -> str:
        if v.lower() in FORBIDDEN_SLUGS:
            raise ValueError("Cruise Guru is not a tenant of this system (Section 13.10)")
        return v

    @field_validator("monthly_content_cap")
    @classmethod
    def _content_cap_low(cls, v: int) -> int:
        if v > 8:
            raise ValueError("monthly_content_cap above 8 is mass generation (Section 13.9)")
        return v


class Project(BaseModel):
    """A projects row as passed to collectors and analysts. Never carries a raw secret."""

    id: UUID
    slug: str
    display_name: str
    domains: list[str]
    vertical: Vertical
    gsc_property: str | None = None
    ga4_property_id: str | None = None
    credentials_ref: str | None = None
    approver_id: UUID
    monthly_cost_cap_usd: float
    enabled_agents: list[str]
    allowed_schema_types: list[str] = Field(default_factory=list)
    monthly_content_cap: int = 4
    critical_paths: list[str] = Field(default_factory=list)
    active: bool = True

    @classmethod
    def from_row(cls, row: dict) -> Project:
        return cls(**{k: row[k] for k in cls.model_fields if k in row})

    @property
    def primary_domain(self) -> str:
        return self.domains[0]
