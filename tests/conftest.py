from __future__ import annotations

import os
import uuid

import pytest

from db.migrate import apply_migrations

TEST_URL = os.environ.get("SEO_TEST_DATABASE_URL")


def _db_available() -> bool:
    if not TEST_URL:
        return False
    try:
        import psycopg
        with psycopg.connect(TEST_URL, connect_timeout=3):
            return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()
requires_db = pytest.mark.skipif(not DB_AVAILABLE, reason="SEO_TEST_DATABASE_URL not reachable")


@pytest.fixture(scope="session")
def db_url() -> str:
    """A freshly migrated test database. The schema is dropped and rebuilt once per session so that
    budget and idempotency tests start from a known state (CI gets a clean container anyway)."""
    assert TEST_URL
    import psycopg
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute("drop schema public cascade; create schema public")
    apply_migrations(TEST_URL, verbose=False)
    return TEST_URL


@pytest.fixture(scope="session")
def worker_url(db_url: str) -> str:
    """Connection string for a LOGIN role that is a member of seo_worker and does NOT bypass RLS."""
    from urllib.parse import urlsplit, urlunsplit

    import psycopg

    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("""
            do $$ begin
              if not exists (select 1 from pg_roles where rolname = 'seo_worker_test') then
                create role seo_worker_test login password 'seo_worker_test' in role seo_worker;
              end if;
            end $$""")
    parts = urlsplit(db_url)
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"seo_worker_test:seo_worker_test@{host}{port}", parts.path, parts.query, ""))


@pytest.fixture()
def seeded(db_url: str) -> dict[str, uuid.UUID]:
    from config.loader import seed_projects
    return seed_projects(db_url)
