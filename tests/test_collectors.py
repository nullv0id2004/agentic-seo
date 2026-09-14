"""M2 acceptance: killing the network mid-crawl produces a gap row and a partial result, never a lost run
or a fabricated value."""
import uuid

import pytest

from collectors.html import extract, sitemap_urls
from collectors.registry import collect
from contracts.project import Project
from db.connection import project_scope
from tests.conftest import requires_db
from tests.fakesite import HOME, FakeSite

pytestmark = [requires_db, pytest.mark.db]


def _project(worker_url, seeded, slug="korum") -> Project:
    with project_scope(seeded[slug], url=worker_url) as s:
        row = s.fetchone("select * from projects where id = %(project_id)s")
    return Project.from_row(row)


def test_html_extract_is_deterministic():
    f = extract(HOME, "https://korum.worldhire.com/", ["korum.worldhire.com"])
    assert f.title == "KORUM: hiring platform"
    assert f.h1 == ["Hiring, done right"]
    assert f.schema_types == ["Organization"]
    assert f.canonical == "https://korum.worldhire.com/"
    assert "https://korum.worldhire.com/recruiter/inbox" in f.internal_links
    assert not any("other.example" in u for u in f.internal_links)
    assert f.word_count > 5
    assert sitemap_urls("<sitemapindex><sitemap><loc>https://a/b.xml</loc></sitemap></sitemapindex>") == ([], ["https://a/b.xml"])


def test_full_crawl_writes_pages_and_sitemap(worker_url, seeded):
    project = _project(worker_url, seeded)
    site = FakeSite()
    run_id = uuid.uuid4()
    res = collect("site_crawl", project, run_id, {"_transport": site.transport}, db_url=worker_url)
    assert res.gaps == 0 and not res.partial
    with project_scope(project.id, url=worker_url) as s:
        pages = s.fetchall("select url, status_code, in_sitemap, x_robots_tag, schema_types from raw_crawl_pages where project_id = %(project_id)s and run_id = %(run_id)s order by url", {"run_id": run_id})
        smap = s.fetchall("select url from raw_sitemap_urls where project_id = %(project_id)s and run_id = %(run_id)s", {"run_id": run_id})
    urls = {p["url"] for p in pages}
    assert {"https://korum.worldhire.com/", "https://korum.worldhire.com/about", "https://korum.worldhire.com/blog/one",
            "https://korum.worldhire.com/blog/two", "https://korum.worldhire.com/recruiter/inbox"} <= urls
    inbox = next(p for p in pages if p["url"].endswith("/recruiter/inbox"))
    assert inbox["x_robots_tag"] == "noindex, nofollow"
    assert len(smap) == 3
    assert next(p for p in pages if p["url"] == "https://korum.worldhire.com/about")["in_sitemap"] is True


def test_network_killed_mid_crawl_gives_gap_and_partial(worker_url, seeded):
    project = _project(worker_url, seeded)
    site = FakeSite(fail_after=3)  # sitemap + 2 pages succeed, then every request raises ConnectError
    run_id = uuid.uuid4()
    res = collect("site_crawl", project, run_id, {"_transport": site.transport}, db_url=worker_url)
    assert res.partial is True
    assert res.gaps >= 1
    with project_scope(project.id, url=worker_url) as s:
        pages = s.fetchall("select url, status_code, title from raw_crawl_pages where project_id = %(project_id)s and run_id = %(run_id)s", {"run_id": run_id})
        gaps = s.fetchall("select collector, reason, affected_scope from collection_gaps where project_id = %(project_id)s and run_id = %(run_id)s", {"run_id": run_id})
    assert 0 < len(pages) < 5, "partial result: some pages, not all"
    assert all(p["status_code"] is not None for p in pages), "no fabricated rows for pages that were never fetched"
    assert gaps and all(g["collector"] == "site_crawl" for g in gaps)
    assert any("ConnectError" in g["reason"] for g in gaps)


def test_unexpected_exception_becomes_gap_not_lost_run(worker_url, seeded):
    from collectors.registry import register
    project = _project(worker_url, seeded)

    def boom(ctx, params):
        ctx.write("raw_crawl_pages", {"url": "https://korum.worldhire.com/x", "status_code": 200})
        raise RuntimeError("collector bug")

    register("_boom", boom)
    run_id = uuid.uuid4()
    res = collect("_boom", project, run_id, {}, db_url=worker_url)
    assert res.partial and res.gaps == 1 and res.rows_written == 1
    with project_scope(project.id, url=worker_url) as s:
        g = s.fetchone("select reason from collection_gaps where project_id = %(project_id)s and run_id = %(run_id)s", {"run_id": run_id})
    assert "RuntimeError: collector bug" in g["reason"]


def test_header_probe_clean_and_leaked(worker_url, seeded):
    project = _project(worker_url, seeded)
    paths = ["/dashboard/", "/recruiter/inbox", "/admin/", "/api/health", "/blog/one"]
    clean = collect("header_probe", project, uuid.uuid4(), {"_transport": FakeSite().transport, "paths": paths}, db_url=worker_url)
    assert clean.detail["violations"] == []
    assert clean.detail["probed"] == 4, "only urls matching a critical rule are probed"
    run_id = uuid.uuid4()
    leaked = collect("header_probe", project, run_id, {"_transport": FakeSite(leaked_inbox=True).transport, "paths": paths}, db_url=worker_url)
    keys = {v["rule_key"] for v in leaked.detail["violations"]}
    assert keys == {"no_indexable_auth_route"}
    assert {v["url"] for v in leaked.detail["violations"]} == {"https://korum.worldhire.com/dashboard/", "https://korum.worldhire.com/recruiter/inbox", "https://korum.worldhire.com/admin/"}
    with project_scope(project.id, url=worker_url) as s:
        audit = s.fetchall("select event from audit_log where project_id = %(project_id)s and run_id = %(run_id)s", {"run_id": run_id})
    assert any(a["event"] == "critical_rule_violation" for a in audit)


def test_header_probe_unreachable_is_gap_and_violation(worker_url, seeded):
    project = _project(worker_url, seeded)
    res = collect("header_probe", project, uuid.uuid4(), {"_transport": FakeSite(fail_after=1).transport, "paths": ["/recruiter/inbox"]}, db_url=worker_url)
    assert res.gaps >= 1
    assert [v["rule_key"] for v in res.detail["violations"]] == ["no_indexable_auth_route"], "unproven is not safe"


def test_doc_fetch_stores_untrusted_text(worker_url, seeded):
    project = _project(worker_url, seeded)
    run_id = uuid.uuid4()
    res = collect("doc_fetch", project, run_id, {"_transport": FakeSite().transport,
                  "urls": ["https://korum.worldhire.com/about", "https://korum.worldhire.com/missing"]}, db_url=worker_url)
    assert res.rows_written == 2 and res.gaps == 1
    with project_scope(project.id, url=worker_url) as s:
        docs = s.fetchall("select url, http_status, content_text, is_untrusted from raw_fetched_documents where project_id = %(project_id)s and run_id = %(run_id)s order by url", {"run_id": run_id})
    assert all(d["is_untrusted"] for d in docs)
    ok = next(d for d in docs if d["url"].endswith("/about"))
    assert "Words words words" in ok["content_text"]
    missing = next(d for d in docs if d["url"].endswith("/missing"))
    assert missing["http_status"] == 404 and missing["content_text"] is None


def test_gsc_without_credentials_is_a_gap_not_a_crash(worker_url, seeded, monkeypatch):
    monkeypatch.setenv("SEO_SECRET_BACKEND", "env")
    from config.settings import get_settings
    get_settings.cache_clear()
    project = _project(worker_url, seeded)
    run_id = uuid.uuid4()
    res = collect("gsc_performance", project, run_id, {}, db_url=worker_url)
    assert res.partial and res.rows_written == 0
    with project_scope(project.id, url=worker_url) as s:
        g = s.fetchone("select reason from collection_gaps where project_id = %(project_id)s and run_id = %(run_id)s", {"run_id": run_id})
    assert "SecretUnavailable" in g["reason"]
    get_settings.cache_clear()
