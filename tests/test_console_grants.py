"""The console role reads every raw table its pages show, writes none, and never sees fetched documents."""
from __future__ import annotations

import pytest

from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

CONSOLE_READS = (
    "raw_gsc_performance", "raw_ga4_daily", "raw_crawl_pages", "raw_sitemap_urls", "raw_vitals", "raw_serp",
    "raw_keyword_metrics", "raw_search_status", "raw_ai_keyword_metrics", "raw_llm_mention_metrics",
    "raw_llm_mentions", "raw_llm_responses", "agent_logs", "audit_log", "mentions", "pitches", "content_briefs",
)


def _console_url(db_url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    import psycopg

    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("""
            do $$ begin
              if not exists (select 1 from pg_roles where rolname = 'seo_console_test') then
                create role seo_console_test login password 'seo_console_test' in role seo_console;
              end if;
            end $$""")
    parts = urlsplit(db_url)
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"seo_console_test:seo_console_test@{parts.hostname or 'localhost'}{port}", parts.path, parts.query, ""))


def test_console_reads_raw_tables_but_writes_none(db_url, seeded):
    import psycopg

    url = _console_url(db_url)
    pid = str(seeded["korum"])
    with psycopg.connect(url) as conn:
        conn.execute("select set_config('app.project_id', %s, false)", (pid,))
        for table in CONSOLE_READS:
            conn.execute(f"select count(*) from {table} where project_id = %s", (pid,)).fetchone()
        for stmt in ("insert into raw_serp (project_id, run_id, query) values (%s, gen_random_uuid(), 'x')",
                     "select count(*) from raw_fetched_documents where project_id = %s"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute(stmt, (pid,))
