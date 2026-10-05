"""M2 acceptance: killing the network mid-crawl produces a gap row and a partial result, never a lost run
or a fabricated value."""
import json
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


def test_header_probe_follows_same_site_redirects(worker_url, seeded):
    """/admin -> 308 -> /admin/ (noindex). The probe judges the final page, so this is clean and recorded."""
    project = _project(worker_url, seeded)
    run_id = uuid.uuid4()
    res = collect("header_probe", project, run_id, {"_transport": FakeSite().transport, "paths": ["/admin", "/cart"]}, db_url=worker_url)
    assert res.detail["violations"] == []
    with project_scope(project.id, url=worker_url) as s:
        row = s.fetchone("select status_code, x_robots_tag, canonical from raw_crawl_pages where project_id = %(project_id)s and run_id = %(run_id)s and url = 'https://korum.worldhire.com/admin'", {"run_id": run_id})
    assert row["status_code"] == 200 and "noindex" in row["x_robots_tag"] and row["canonical"] == "https://korum.worldhire.com/admin/"
    # a leaked final page behind a redirect is still caught
    res = collect("header_probe", project, uuid.uuid4(), {"_transport": FakeSite(leaked_inbox=True).transport, "paths": ["/recruiter"]}, db_url=worker_url)
    assert [v["url"] for v in res.detail["violations"]] == ["https://korum.worldhire.com/recruiter"]


def test_gsc_collects_page_grain_totals_and_query_grain_keywords(worker_url, seeded, monkeypatch):
    """Production 2026-09-22: 28 days with impressions in Search Console, zero rows collected, because the
    query grain drops anonymised queries. The page grain carries the totals; the query grain the keywords."""
    import httpx

    import collectors.gsc as gsc

    monkeypatch.setattr(gsc, "access_token", lambda ref, scope: "token")
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["dimensions"])
        if "query" in body["dimensions"]:
            return httpx.Response(200, json={"rows": []})  # every query on this small site is anonymised
        return httpx.Response(200, json={"rows": [
            {"keys": ["2026-09-19", "https://korum.worldhire.com/"], "clicks": 1, "impressions": 2, "ctr": 0.5, "position": 8.0},
            {"keys": ["2026-09-19", "https://korum.worldhire.com/jobs"], "clicks": 0, "impressions": 3, "ctr": 0, "position": 21.5},
        ]})

    project = _project(worker_url, seeded)
    run_id = uuid.uuid4()
    res = collect("gsc_performance", project, run_id, {"_transport": httpx.MockTransport(handle), "start_date": "2026-09-19", "end_date": "2026-09-19"}, db_url=worker_url)
    assert not res.partial and res.rows_written == 2 and res.detail["page_rows"] == 2 and res.detail["query_rows"] == 0
    assert calls == [["date", "page"], ["date", "query", "page"]], "no country/device: each added dimension loses rows to the privacy threshold"
    with project_scope(project.id, url=worker_url) as s:
        rows = s.fetchall("select query, page, impressions from raw_gsc_performance where project_id = %(project_id)s and run_id = %(run_id)s order by page", {"run_id": run_id})
    assert [r["query"] for r in rows] == [None, None] and sum(r["impressions"] for r in rows) == 5


def test_ga4_filters_to_the_project_hosts(worker_url, seeded, monkeypatch):
    """Property 546390504 has one stream for worldhire.com and korum.worldhire.com; korum's rows must
    only count korum's hosts."""
    import httpx

    import collectors.ga4 as ga4

    monkeypatch.setattr(ga4, "access_token", lambda ref, scope: "token")
    seen = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"rows": [{"dimensionValues": [{"value": "20260921"}, {"value": "/jobs"}, {"value": "Organic Search"}],
                                                   "metricValues": [{"value": "4"}, {"value": "3"}, {"value": "0"}]}]})

    project = _project(worker_url, seeded)
    with project_scope(project.id, url=worker_url) as s:
        s.execute("update projects set ga4_property_id = '546390504' where id = %(project_id)s")
    project = _project(worker_url, seeded)
    run_id = uuid.uuid4()
    res = collect("ga4", project, run_id, {"_transport": httpx.MockTransport(handle), "start_date": "2026-09-21", "end_date": "2026-09-21"}, db_url=worker_url)
    assert not res.partial and res.rows_written == 1
    flt = seen["body"]["dimensionFilter"]["filter"]
    assert flt["fieldName"] == "hostName" and flt["inListFilter"]["values"] == ["korum.worldhire.com"]
    with project_scope(project.id, url=worker_url) as s:
        row = s.fetchone("select date, page_path, channel, sessions from raw_ga4_daily where project_id = %(project_id)s and run_id = %(run_id)s", {"run_id": run_id})
        s.execute("update projects set ga4_property_id = null where id = %(project_id)s")
    assert str(row["date"]) == "2026-09-21" and row["page_path"] == "/jobs" and row["sessions"] == 4


def _dfs_task(result: dict) -> dict:
    return {"status_code": 20000, "cost": 0.002, "tasks": [{"status_code": 20000, "status_message": "Ok.", "cost": 0.002, "result": [result]}]}


def test_ai_keyword_metrics_writes_ai_search_volume(worker_url, seeded):
    import httpx

    seen = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)[0]
        return httpx.Response(200, json=_dfs_task({"location_code": 2840, "language_code": "en", "items": [
            {"keyword": "job search platform", "ai_search_volume": 320, "ai_monthly_searches": [{"year": 2026, "month": 9, "ai_search_volume": 320}]},
            {"keyword": "recruiter software", "ai_search_volume": None, "ai_monthly_searches": []},
        ]}))

    project = _project(worker_url, seeded)
    run_id = uuid.uuid4()
    res = collect("ai_keyword_metrics", project, run_id, {"_transport": httpx.MockTransport(handle), "keywords": ["Job Search Platform", "recruiter software"]}, db_url=worker_url)
    assert not res.partial and res.rows_written == 2
    assert seen["body"]["keywords"][:2] == ["job search platform", "recruiter software"] and seen["body"]["location_code"] == 2840
    with project_scope(project.id, url=worker_url) as s:
        rows = s.fetchall("select keyword, ai_search_volume, monthly from raw_ai_keyword_metrics where project_id = %(project_id)s and run_id = %(run_id)s order by keyword", {"run_id": run_id})
    assert rows[0]["keyword"] == "job search platform" and rows[0]["ai_search_volume"] == 320 and rows[0]["monthly"][0]["month"] == 9
    assert rows[1]["ai_search_volume"] is None


def test_llm_mentions_searches_each_target_and_keeps_only_this_brand(worker_url, seeded):
    """Run b45765f5: the brand aggregate counted KORUM the fishing tackle maker, and one search_mentions call
    with both targets (combined with AND) returned nothing."""
    import httpx

    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)[0]
        calls.append((request.url.path.rsplit("/", 2)[-2], body["target"], body.get("limit")))
        if request.url.path.endswith("/target_metrics_lite/live"):
            return httpx.Response(200, json=_dfs_task({"items": [
                {"location": 2840, "language": "en", "platform": "chat_gpt", "metrics": {"mentions": 3, "ai_search_volume": 1200}},
                {"location": 2840, "language": "en", "platform": "google", "metrics": {"mentions": 1, "ai_search_volume": 40}},
            ]}))
        if "domain" in body["target"][0]:
            return httpx.Response(200, json=_dfs_task({"items": [
                {"platform": "chat_gpt", "model_name": "gpt-4o", "question": "best hiring platforms in india", "answer": "Options include KORUM.", "ai_search_volume": 500,
                 "sources": [{"rank": 1, "domain": "www.korum.worldhire.com", "url": "https://korum.worldhire.com/jobs", "title": "Jobs"}],
                 "brand_entities": [{"rank": 1, "title": "KORUM", "category": "recruitment"}]},
            ]}))
        return httpx.Response(200, json=_dfs_task({"items": [
            {"platform": "google", "model_name": "google_ai_overview", "question": "best carp rods", "answer": "Korum makes rods and reels for carp anglers.",
             "ai_search_volume": 9000, "sources": [{"rank": 1, "domain": "korum.co.uk", "url": "https://korum.co.uk/", "title": "Korum"}]},
            {"platform": "chat_gpt", "model_name": "gpt-4o", "question": "new job sites in india", "answer": "KORUM by WorldHire lists jobs for job seekers.",
             "ai_search_volume": 30, "sources": []},
            {"platform": "google", "model_name": "google_ai_overview", "question": "the flying machine reviews", "ai_search_volume": 50, "sources": [],
             "answer": "Stores such as Korum Mall score well. " + "Denim fits and fabric quality vary by line. " * 6 + "Employee ratings: job security ~4.0 / 5."},
            {"platform": "chat_gpt", "model_name": "gpt-4o", "question": "korum app india", "ai_search_volume": 20, "sources": [],
             "answer": "KORUM is a hiring app where employers post roles."},
        ]}))

    project = _project(worker_url, seeded)
    assert project.brand_context_terms[0] == "WorldHire"
    run_id = uuid.uuid4()
    res = collect("llm_mentions", project, run_id, {"_transport": httpx.MockTransport(handle)}, db_url=worker_url)
    assert not res.partial and res.rows_written == 7, "2 domain metrics rows, 1 domain mention, 4 brand mentions"
    assert [c[2] for c in calls[1:]] == [100, 20], "brand rows are billed and mostly other brands: 20, not 100"
    assert [(c[0], c[1]) for c in calls] == [
        ("target_metrics_lite", [{"domain": "korum.worldhire.com", "include_subdomains": True}]),
        ("search_mentions", [{"domain": "korum.worldhire.com", "include_subdomains": True}]),
        ("search_mentions", [{"keyword": "KORUM", "search_scope": ["answer", "brand_entities"]}]),
    ], "no brand aggregate, and one search per target"
    assert round(res.cost_usd, 6) == 0.006, "every DataForSEO call's cost reaches the collector result"
    with project_scope(project.id, url=worker_url) as s:
        metrics = s.fetchall("select target_kind, platform, mentions from raw_llm_mention_metrics where project_id = %(project_id)s and run_id = %(run_id)s order by platform", {"run_id": run_id})
        mentions = s.fetchall("select target_kind, question, cites_project, about_project, brand_entities from raw_llm_mentions where project_id = %(project_id)s and run_id = %(run_id)s order by target_kind, question", {"run_id": run_id})
    assert [(m["target_kind"], m["platform"], m["mentions"]) for m in metrics] == [("domain", "chat_gpt", 3), ("domain", "google", 1)]
    by_q = {m["question"]: m for m in mentions}
    assert by_q["best hiring platforms in india"]["target_kind"] == "domain" and by_q["best hiring platforms in india"]["cites_project"] is True
    assert by_q["best hiring platforms in india"]["brand_entities"][0]["title"] == "KORUM"
    assert by_q["best carp rods"]["about_project"] is False, "the fishing tackle Korum is not this project"
    assert by_q["new job sites in india"]["about_project"] is True and by_q["new job sites in india"]["cites_project"] is False
    assert by_q["the flying machine reviews"]["about_project"] is False, "run 7bdd20ae: Korum Mall and job security were paragraphs apart"
    assert by_q["korum app india"]["about_project"] is True, "a generic context term next to the brand counts"


def test_brand_prompts_skip_branded_and_misspelled_keywords():
    from collectors.llm_responses import brand_tokens, build_prompts, is_branded

    tokens = brand_tokens("KORUM", ["korum.worldhire.com"], ["WorldHire", "job"])
    assert tokens == {"korum", "worldhire"}
    assert all(is_branded(k, tokens) for k in ("kourm", "worldhire", "world hire", "korum jobs", "korm"))
    assert not any(is_branded(k, tokens) for k in ("job search platform", "hiring platform india", "forum", "form builder", "work hire"))
    assert build_prompts(["kourm", "job search platform", "worldhire"], "KORUM", qualifier="WorldHire", tokens=tokens) == [
        "What is KORUM (WorldHire) and what does it offer?",
        "What are the best options for job search platform in India?",
        "Which websites should I use for job search platform?",
    ]


def test_llm_responses_asks_deterministic_prompts_and_marks_citations(worker_url, seeded):
    import httpx

    from collectors.llm_responses import build_prompts

    assert build_prompts(["job search platform"], "KORUM") == [
        "What is KORUM and what does it offer?",
        "What are the best options for job search platform in India?",
        "Which websites should I use for job search platform?",
    ]
    asked = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)[0]
        asked.append(body)
        cited = "KORUM" in body["user_prompt"]
        annotations = [{"title": "korum.worldhire.com", "url": "https://korum.worldhire.com/", "text": "KORUM", "start_index": 0, "end_index": 5}] if cited else []
        return httpx.Response(200, json=_dfs_task({"model_name": "gpt-4.1-2025-04-14", "input_tokens": 50, "output_tokens": 200, "web_search": True, "money_spent": 0.01,
                                                   "items": [{"type": "message", "sections": [{"type": "text", "text": "An answer.", "annotations": annotations}],
                                                              "fan_out_queries": ["korum hiring platform"]}]}))

    project = _project(worker_url, seeded)
    run_id = uuid.uuid4()
    res = collect("llm_responses", project, run_id, {"_transport": httpx.MockTransport(handle), "keywords": ["job search platform"]}, db_url=worker_url)
    assert not res.partial and res.rows_written == 3
    assert all(b["web_search"] is True and b["force_web_search"] is True and b["model_name"] == "gpt-4.1" and b["web_search_country_iso_code"] == "IN" for b in asked)
    assert asked[0]["user_prompt"] == "What is KORUM (WorldHire) and what does it offer?"
    assert round(res.cost_usd, 6) == 0.006
    with project_scope(project.id, url=worker_url) as s:
        rows = s.fetchall("select prompt, cites_project, citations, fan_out_queries, cost_usd from raw_llm_responses where project_id = %(project_id)s and run_id = %(run_id)s order by cites_project desc, prompt", {"run_id": run_id})
    assert rows[0]["prompt"].startswith("What is KORUM") and rows[0]["cites_project"] is True and rows[0]["citations"][0]["url"] == "https://korum.worldhire.com/"
    assert all(r["cites_project"] is False for r in rows[1:]) and rows[0]["fan_out_queries"] == ["korum hiring platform"] and float(rows[0]["cost_usd"]) == 0.002


def test_paid_api_cost_reaches_the_agent_log_and_the_run_budget(worker_url, seeded):
    """Run b45765f5 spent about USD 0.53 at DataForSEO and recorded USD 0.0006: only model tokens were
    counted, so the monthly budget check could not see collector spend."""
    import httpx

    from orchestrator.runtime import Runtime

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_dfs_task({"items": []}))

    project = _project(worker_url, seeded)
    run_id = uuid.uuid4()
    rt = Runtime(db_url=worker_url, model="fake", collector_overrides={"*": {"_transport": httpx.MockTransport(handle)}})
    res = rt.run_collector(project, run_id, "llm_mentions")
    assert round(res.cost_usd, 6) == 0.006
    with project_scope(project.id, url=worker_url) as s:
        row = s.fetchone("select cost_usd from agent_logs where project_id = %(project_id)s and run_id = %(run_id)s and agent = 'llm_mentions'", {"run_id": run_id})
    assert float(row["cost_usd"]) == 0.006
