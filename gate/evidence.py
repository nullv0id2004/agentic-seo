"""Resolve evidence refs against the raw_* tables of one project. The only database-aware part of stage 1."""
from __future__ import annotations

from uuid import UUID

from db.connection import RAW_TABLES, ProjectScope


def make_resolver(scope: ProjectScope):
    tables = sorted(RAW_TABLES)

    def resolve(refs: set[UUID]) -> set[UUID]:
        if not refs:
            return set()
        union = " union all ".join(f"select id from {t} where project_id = %(project_id)s and id = any(%(ids)s)" for t in tables)
        rows = scope.fetchall(union, {"ids": list(refs)})
        return {r["id"] for r in rows}

    return resolve


def load_project_rules(scope: ProjectScope):
    from gate.stage1_rules import ProjectRules

    brand = scope.fetchall("select rule_type, pattern, severity, rationale, active from brand_rules where project_id = %(project_id)s and active")
    crit = scope.fetchall("select rule_key, url_pattern, assertion, active from critical_rules where project_id = %(project_id)s and active")
    slug = scope.fetchone("select slug from projects where id = %(project_id)s")["slug"]
    return ProjectRules(brand_rules=brand, critical_rules=crit, slug=slug)
