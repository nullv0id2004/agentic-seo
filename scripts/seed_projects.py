#!/usr/bin/env python3
"""Seed projects, brand_rules and critical_rules from config/projects/*.yaml. Idempotent; operator action.

  SEO_DATABASE_URL=... python scripts/seed_projects.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.loader import load_all_project_configs, seed_projects  # noqa: E402


def _lit(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "array[" + ", ".join(_lit(x) for x in v) + "]::text[]" if v else "'{}'::text[]"
    return "'" + str(v).replace("'", "''") + "'"


def print_sql() -> None:
    """Emit the seed as idempotent SQL, for running through a SQL console or the Supabase connector."""
    for cfg in load_all_project_configs():
        cols = {
            "slug": cfg.slug, "display_name": cfg.display_name or cfg.slug, "domains": cfg.domains, "vertical": cfg.vertical,
            "gsc_property": cfg.gsc_property, "ga4_property_id": cfg.ga4_property_id, "credentials_ref": cfg.credentials_ref,
            "approver_id": str(cfg.approver_id), "monthly_cost_cap_usd": cfg.monthly_cost_cap_usd, "enabled_agents": cfg.enabled_agents,
            "allowed_schema_types": cfg.allowed_schema_types, "monthly_content_cap": cfg.monthly_content_cap,
            "critical_paths": cfg.critical_paths, "github_repo": cfg.github_repo, "cms_publish_url": cfg.cms_publish_url,
        }
        updates = ", ".join(f"{k} = excluded.{k}" for k in cols if k != "slug")
        print(f"insert into projects ({', '.join(cols)}) values ({', '.join(_lit(v) for v in cols.values())})")
        print(f"  on conflict (slug) do update set {updates};")
        print(f"delete from brand_rules where project_id = (select id from projects where slug = {_lit(cfg.slug)});")
        for r in cfg.brand_rules:
            print(f"insert into brand_rules (project_id, rule_type, pattern, severity, rationale, active) select id, {_lit(r.rule_type)}, {_lit(r.pattern)}, {_lit(r.severity)}, {_lit(r.rationale)}, {_lit(r.active)} from projects where slug = {_lit(cfg.slug)};")
        print(f"delete from critical_rules where project_id = (select id from projects where slug = {_lit(cfg.slug)});")
        for c in cfg.critical_rules:
            print(f"insert into critical_rules (project_id, rule_key, url_pattern, assertion, active) select id, {_lit(c.rule_key)}, {_lit(c.url_pattern)}, {_lit(c.assertion)}, {_lit(c.active)} from projects where slug = {_lit(cfg.slug)};")
        print()


if __name__ == "__main__":
    if "--print-sql" in sys.argv:
        print_sql()
        sys.exit(0)
    url = os.environ.get("SEO_DATABASE_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not url:
        print("SEO_DATABASE_URL is required (or pass --print-sql)")
        sys.exit(2)
    for slug, pid in seed_projects(url).items():
        print(f"{slug}: {pid}")
