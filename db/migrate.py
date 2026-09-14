"""Apply db/migrations/*.sql in order, recording each in schema_migrations. Idempotent."""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def apply_migrations(url: str, verbose: bool = True) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(url, autocommit=False) as conn:
        conn.execute("create table if not exists schema_migrations (name text primary key, applied_at timestamptz not null default now())")
        done = {r[0] for r in conn.execute("select name from schema_migrations").fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            conn.commit()
            applied.append(path.name)
            if verbose:
                print(f"applied {path.name}")
    return applied


if __name__ == "__main__":
    import os
    apply_migrations(os.environ.get("SEO_DATABASE_URL") or sys.argv[1])
