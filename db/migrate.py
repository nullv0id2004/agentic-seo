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
    setup_checkpointer(url, verbose)
    return applied


def setup_checkpointer(url: str, verbose: bool = True) -> None:
    """Create the LangGraph checkpoint tables as the migration owner and grant them to the worker.

    The worker role cannot create tables (no CREATE on the schema), so setup() runs here, not at
    runtime. Checkpoint rows are keyed by thread_id = run id; they are LangGraph internals, not
    tenant tables, and the worker only ever reads its own run's thread.
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("grant select, insert, update, delete on checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations to seo_worker")
    if verbose:
        print("checkpointer tables ready")


if __name__ == "__main__":
    import os
    apply_migrations(os.environ.get("SEO_DATABASE_URL") or sys.argv[1])
