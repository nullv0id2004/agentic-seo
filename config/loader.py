"""Load config/projects/*.yaml into ProjectConfig objects and seed the database from them."""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

import yaml

from contracts.project import ProjectConfig

PROJECTS_DIR = Path(__file__).parent / "projects"


def load_project_config(path: Path) -> ProjectConfig:
    data = yaml.safe_load(path.read_text())
    return ProjectConfig.model_validate(data)


def load_all_project_configs(directory: Path = PROJECTS_DIR) -> list[ProjectConfig]:
    return [load_project_config(p) for p in sorted(directory.glob("*.yaml"))]


def seed_projects(url: str, configs: list[ProjectConfig] | None = None) -> dict[str, UUID]:
    """Upsert projects and replace their rule sets. Returns slug -> project id.

    Uses a plain connection as the migration owner: seeding is an operator action, not a run.
    """
    import psycopg
    from psycopg.rows import dict_row

    configs = configs if configs is not None else load_all_project_configs()
    ids: dict[str, UUID] = {}
    with psycopg.connect(url, row_factory=dict_row) as conn:
        for cfg in configs:
            row = conn.execute(
                """
                insert into projects (slug, display_name, domains, vertical, gsc_property, ga4_property_id,
                    credentials_ref, approver_id, monthly_cost_cap_usd, enabled_agents, allowed_schema_types,
                    monthly_content_cap)
                values (%(slug)s, %(display_name)s, %(domains)s, %(vertical)s, %(gsc_property)s, %(ga4_property_id)s,
                    %(credentials_ref)s, %(approver_id)s, %(monthly_cost_cap_usd)s, %(enabled_agents)s,
                    %(allowed_schema_types)s, %(monthly_content_cap)s)
                on conflict (slug) do update set
                    display_name = excluded.display_name, domains = excluded.domains, vertical = excluded.vertical,
                    gsc_property = excluded.gsc_property, ga4_property_id = excluded.ga4_property_id,
                    credentials_ref = excluded.credentials_ref, approver_id = excluded.approver_id,
                    monthly_cost_cap_usd = excluded.monthly_cost_cap_usd, enabled_agents = excluded.enabled_agents,
                    allowed_schema_types = excluded.allowed_schema_types, monthly_content_cap = excluded.monthly_content_cap
                returning id
                """,
                {
                    "slug": cfg.slug, "display_name": cfg.display_name or cfg.slug, "domains": cfg.domains,
                    "vertical": cfg.vertical, "gsc_property": cfg.gsc_property, "ga4_property_id": cfg.ga4_property_id,
                    "credentials_ref": cfg.credentials_ref, "approver_id": cfg.approver_id,
                    "monthly_cost_cap_usd": cfg.monthly_cost_cap_usd, "enabled_agents": cfg.enabled_agents,
                    "allowed_schema_types": cfg.allowed_schema_types, "monthly_content_cap": cfg.monthly_content_cap,
                },
            ).fetchone()
            pid = row["id"]
            ids[cfg.slug] = pid
            conn.execute("select set_config('app.project_id', %s, true)", (str(pid),))
            conn.execute("delete from brand_rules where project_id = %s", (pid,))
            for r in cfg.brand_rules:
                conn.execute(
                    "insert into brand_rules (project_id, rule_type, pattern, severity, rationale, active) values (%s,%s,%s,%s,%s,%s)",
                    (pid, r.rule_type, r.pattern, r.severity, r.rationale, r.active),
                )
            conn.execute("delete from critical_rules where project_id = %s", (pid,))
            for c in cfg.critical_rules:
                conn.execute(
                    "insert into critical_rules (project_id, rule_key, url_pattern, assertion, active) values (%s,%s,%s,%s,%s)",
                    (pid, c.rule_key, c.url_pattern, c.assertion, c.active),
                )
        conn.commit()
    return ids
