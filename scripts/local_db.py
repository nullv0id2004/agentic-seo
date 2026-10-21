#!/usr/bin/env python3
"""Set up a LOCAL development database: migrations, the worker and console login roles, and the projects.

  docker compose up -d
  python scripts/local_db.py                                          # postgresql://postgres@localhost:5433/seo_dev
  python scripts/local_db.py postgresql://postgres@localhost:5432/seo_dev

Idempotent. Refuses any host but localhost: the production database is migrated and seeded by hand (README,
Deploying), and nothing may be applied to the Korum application databases (Section 13.1).
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.loader import load_all_project_configs, seed_projects  # noqa: E402
from db.migrate import apply_migrations  # noqa: E402

DEFAULT_URL = "postgresql://postgres@localhost:5433/seo_dev"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
PASSWORD = "local"   # local only; production login roles get real passwords (README, Deploying)
ROLES = {"seo_worker_login": "seo_worker", "seo_console_login": "seo_console"}


def login_url(admin_url: str, role: str) -> str:
    parts = urlsplit(admin_url)
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"{role}:{PASSWORD}@{parts.hostname}{port}", parts.path, parts.query, ""))


def main(argv: list[str]) -> int:
    url = argv[0] if argv else DEFAULT_URL
    host = urlsplit(url).hostname
    if host not in LOCAL_HOSTS:
        print(f"refusing {host!r}: this script only sets up a database on localhost")
        return 2

    import psycopg

    apply_migrations(url)
    with psycopg.connect(url, autocommit=True) as conn:
        for login, group in ROLES.items():
            conn.execute(f"""do $$ begin
                if not exists (select 1 from pg_roles where rolname = '{login}') then
                  create role {login} login password '{PASSWORD}' in role {group};
                end if; end $$""")
    ids = seed_projects(url)
    print(f"seeded {', '.join(sorted(ids))}")

    approvers = {c.slug: c.approver_id for c in load_all_project_configs()}
    print("\nworker (.env):")
    print(f"  SEO_DATABASE_URL={login_url(url, 'seo_worker_login')}")
    print("\nconsole (console/.env.local):")
    print(f"  CONSOLE_DATABASE_URL={login_url(url, 'seo_console_login')}")
    for slug in sorted(approvers):
        print(f"  CONSOLE_APPROVER_ID={approvers[slug]}   # to decide {slug}'s approvals")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
