"""M1 acceptance: a query without a project_id scope returns zero rows under RLS."""
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from db.connection import ProjectScope, RawTableWriteError, UnscopedQueryError, project_scope
from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


def test_unscoped_query_sees_zero_rows(worker_url, seeded):
    with psycopg.connect(worker_url, row_factory=dict_row) as conn:
        assert conn.execute("select count(*) as n from projects").fetchone()["n"] == 0
        assert conn.execute("select count(*) as n from brand_rules").fetchone()["n"] == 0
        assert conn.execute("select count(*) as n from critical_rules").fetchone()["n"] == 0
        # a bare select with an explicit filter on another project's id still sees nothing
        other = seeded["rejuveluxe"]
        assert conn.execute("select count(*) as n from brand_rules where project_id = %s", (other,)).fetchone()["n"] == 0


def test_scoped_query_sees_only_its_project(worker_url, seeded):
    korum = seeded["korum"]
    with project_scope(korum, url=worker_url) as s:
        rows = s.fetchall("select pattern from brand_rules where project_id = %(project_id)s")
        assert len(rows) == 4
        # RLS also blocks reading another project's rows even with an explicit filter
        rl = s.fetchall("select pattern from brand_rules where project_id = %(other)s", {"other": seeded["rejuveluxe"]})
        assert rl == []


def test_scope_refuses_statement_without_project_id(worker_url, seeded):
    with project_scope(seeded["korum"], url=worker_url) as s, pytest.raises(UnscopedQueryError):
        s.execute("select * from issues")


def test_only_collectors_write_raw_tables(worker_url, seeded):
    with project_scope(seeded["korum"], caller="analyst:technical", url=worker_url) as s, pytest.raises(RawTableWriteError):
        s.insert("raw_crawl_pages", {"run_id": uuid.uuid4(), "url": "https://korum.worldhire.com/"})


def test_insert_for_another_project_is_rejected_by_rls(worker_url, seeded):
    with psycopg.connect(worker_url, row_factory=dict_row) as conn:
        conn.execute("select set_config('app.project_id', %s, false)", (str(seeded["korum"]),))
        s = ProjectScope(conn, seeded["rejuveluxe"], caller="collector:test")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            s.insert("collection_gaps", {"run_id": uuid.uuid4(), "collector": "x", "reason": "y"})
        conn.rollback()


def test_fetched_documents_are_always_untrusted(worker_url, seeded):
    with project_scope(seeded["korum"], caller="collector:doc_fetch", url=worker_url) as s, pytest.raises(psycopg.errors.CheckViolation):
        s.insert("raw_fetched_documents", {"run_id": uuid.uuid4(), "url": "https://x", "is_untrusted": False})
