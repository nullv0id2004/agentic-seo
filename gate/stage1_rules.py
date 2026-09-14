"""Gate stage 1: the deterministic rule engine. Pure Python, no model, no network.

Runs against every analyst output, in this order:
  1. schema validation against the Pydantic contract
  2. brand_rules regex sweep (severity=block halts the artifact)
  3. URL allowlist: any url matching a must_noindex / must_not_appear_in_sitemap critical rule halts
  4. evidence completeness: assertions without evidence_ref are dropped, dangling refs halt
  5. numeric sanity: volumes, percentages, dates within plausible bounds

Rules are rows, not prompts. This module receives them as plain dicts so that it can be unit tested
against fixtures without a database.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from contracts.artifacts import ARTIFACT_MODELS
from rules.indexability import path_of

URL_LIKE = re.compile(r"(?:https?://[^\s\"'<>)]+|(?<![\w./])/[A-Za-z0-9_\-./?=&%]+)")
PROTECTED_ASSERTIONS = {"must_noindex", "must_not_appear_in_sitemap"}

# fields that quote raw or third-party text verbatim; they are evidence, not copy, and are not brand-swept
QUOTED_FIELDS = {"evidence", "supporting_span", "current", "claim"}

NUMERIC_BOUNDS: dict[str, tuple[float, float]] = {
    "volume": (0, 100_000_000), "volume_low": (0, 100_000_000), "volume_high": (0, 100_000_000),
    "ctr": (0, 1), "position": (0, 1000), "cls": (0, 10), "lcp_ms": (0, 120_000), "inp_ms": (0, 60_000),
    "difficulty": (0, 100), "domain_rating": (0, 100), "cost_usd": (0, 10_000), "clicks": (0, 1e9),
    "impressions": (0, 1e10), "sessions": (0, 1e9), "percent": (-100, 100_000), "pct": (-100, 100_000),
}
DATE_FIELDS = {"observed_on", "cutoff_date", "first_seen", "date"}


@dataclass(frozen=True)
class Violation:
    check: str                 # 'schema' | 'brand' | 'url_allowlist' | 'evidence' | 'numeric'
    severity: str              # 'block' | 'warn'
    path: str                  # json path inside the artifact
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"check": self.check, "severity": self.severity, "path": self.path, "detail": self.detail}


@dataclass
class Stage1Result:
    blocked: bool
    violations: list[Violation]
    artifact: dict[str, Any] | None      # cleaned artifact (evidence-less assertions dropped), None when blocked
    dropped: int = 0
    evidence_refs: set[UUID] = field(default_factory=set)

    @property
    def blocking(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "block"]


@dataclass
class ProjectRules:
    brand_rules: list[dict[str, Any]]
    critical_rules: list[dict[str, Any]]
    slug: str = ""


EvidenceResolver = Callable[[set[UUID]], set[UUID]]


def run_stage1(agent: str, artifact: BaseModel | dict[str, Any], rules: ProjectRules,
               resolve_evidence: EvidenceResolver, today: date | None = None) -> Stage1Result:
    violations: list[Violation] = []

    # 1. schema
    model_cls = ARTIFACT_MODELS.get(agent)
    if model_cls is None:
        return Stage1Result(True, [Violation("schema", "block", "$", f"unknown agent {agent!r}")], None)
    try:
        model = artifact if isinstance(artifact, BaseModel) else model_cls.model_validate(artifact)
    except ValidationError as e:
        errs = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
        return Stage1Result(True, [Violation("schema", "block", "$", errs)], None)
    data = model.model_dump(mode="json")

    # 2. brand rules
    for path, value in _string_leaves(data):
        if _leaf_name(path) in QUOTED_FIELDS:
            continue
        for rule in rules.brand_rules:
            if not rule.get("active", True):
                continue
            hit = _brand_hit(rule, value)
            if hit:
                violations.append(Violation("brand", rule["severity"], path, f"{rule['rule_type']} {rule['pattern']!r} matched {hit!r}"))
    required = [r for r in rules.brand_rules if r.get("active", True) and r["rule_type"] == "required_phrase"]
    if required:
        corpus = " ".join(v for p, v in _string_leaves(data) if _leaf_name(p) in {"draft", "body", "answer_block"})
        for rule in required:
            if corpus and not re.search(rule["pattern"], corpus, re.IGNORECASE):
                violations.append(Violation("brand", rule["severity"], "$", f"required phrase {rule['pattern']!r} absent"))

    # 3. url allowlist
    protected = [r for r in rules.critical_rules if r.get("active", True) and r["assertion"] in PROTECTED_ASSERTIONS]
    for path, value in _string_leaves(data):
        for url in URL_LIKE.findall(value):
            p = path_of(url)
            for rule in protected:
                if re.search(rule["url_pattern"], p):
                    violations.append(Violation("url_allowlist", "block", path, f"{url!r} matches {rule['rule_key']} ({rule['url_pattern']})"))

    # 4. evidence completeness
    cleaned, dropped, refs, missing_paths = _drop_evidence_less(data)
    for p in missing_paths:
        violations.append(Violation("evidence", "warn", p, "assertion without evidence_ref dropped"))
    if refs:
        found = set(resolve_evidence(refs))
        for ref in sorted(refs - found, key=str):
            violations.append(Violation("evidence", "block", "$", f"evidence_ref {ref} does not resolve to a raw row"))

    # 5. numeric sanity
    today = today or date.today()
    for path, value in _number_leaves(data):
        name = _leaf_name(path)
        bounds = NUMERIC_BOUNDS.get(name) or next((b for k, b in NUMERIC_BOUNDS.items() if name.endswith("_" + k)), None)
        if bounds and not (bounds[0] <= value <= bounds[1]):
            violations.append(Violation("numeric", "block", path, f"{name}={value} outside {bounds}"))
    for path, value in _string_leaves(data):
        if _leaf_name(path) in DATE_FIELDS:
            try:
                d = date.fromisoformat(value[:10])
            except ValueError:
                violations.append(Violation("numeric", "block", path, f"unparseable date {value!r}"))
                continue
            if d.year < 2000 or d > today.replace(year=today.year + 1):
                violations.append(Violation("numeric", "block", path, f"implausible date {value!r}"))

    blocked = any(v.severity == "block" for v in violations)
    return Stage1Result(blocked, violations, None if blocked else cleaned, dropped, refs)


# ---------- helpers ----------

def _brand_hit(rule: dict[str, Any], text: str) -> str | None:
    kind, pattern = rule["rule_type"], rule["pattern"]
    if kind == "char_ban":
        return pattern if pattern in text else None
    if kind == "banned_phrase":
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(0) if m else None
    if kind == "regex":
        m = re.search(pattern, text)
        return m.group(0) if m else None
    return None


def _leaf_name(path: str) -> str:
    last = path.rsplit(".", 1)[-1]
    return re.sub(r"\[\d+\]$", "", last)


def _walk(node: Any, path: str = "$") -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node


def _string_leaves(data: Any) -> list[tuple[str, str]]:
    return [(p, v) for p, v in _walk(data) if isinstance(v, str)]


def _number_leaves(data: Any) -> list[tuple[str, float]]:
    return [(p, v) for p, v in _walk(data) if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _drop_evidence_less(data: dict[str, Any]) -> tuple[dict[str, Any], int, set[UUID], list[str]]:
    """Remove list items that carry an evidence_ref key set to null. Collect every ref that is set.

    Content sources cite fetched_doc_id, which is an evidence ref by another name.
    """
    refs: set[UUID] = set()
    missing: list[str] = []
    dropped = 0

    def clean(node: Any, path: str) -> Any:
        nonlocal dropped
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in ("evidence_ref", "fetched_doc_id") and v:
                    refs.add(UUID(str(v)))
                out[k] = clean(v, f"{path}.{k}")
            return out
        if isinstance(node, list):
            kept = []
            for i, item in enumerate(node):
                p = f"{path}[{i}]"
                if isinstance(item, dict) and "evidence_ref" in item and not item["evidence_ref"]:
                    dropped += 1
                    missing.append(p)
                    continue
                kept.append(clean(item, p))
            return kept
        return node

    return clean(data, "$"), dropped, refs, missing
