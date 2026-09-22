"""M6: keyword volumes only from raw rows, content statistics only from fetched documents, offpage cap in
code, ecommerce checks, content cap, and the quarterly / content graphs end to end."""
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from analysts.base import AnalystContext, AnalystInput
from analysts.content import ContentCapReached, strip_unbacked_numbers
from analysts.content import run as run_content
from analysts.ecommerce import detect as detect_ecom
from analysts.keyword import run as run_keyword
from analysts.offpage import run as run_offpage
from analysts.onpage import candidates
from contracts.project import Project
from db.connection import project_scope
from llm.fake import FakeLLM
from orchestrator.runner import run_workflow
from orchestrator.runtime import Runtime
from tests.conftest import requires_db
from tests.fakesite import FakeSite

pytestmark = [requires_db, pytest.mark.db]


def _project(worker_url, seeded, slug="korum") -> Project:
    with project_scope(seeded[slug], url=worker_url) as s:
        return Project.from_row(s.fetchone("select * from projects where id = %(project_id)s"))


def test_keyword_analyst_never_emits_a_volume_not_in_raw_rows(worker_url, seeded):
    project = _project(worker_url, seeded)
    m1, m2, m3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = {
        "raw_keyword_metrics": [
            {"id": m1, "keyword": "hiring platform", "volume": 1200, "volume_is_range": False, "volume_low": None, "volume_high": None, "difficulty": 40},
            {"id": m2, "keyword": "recruiter inbox login", "volume": 300, "volume_is_range": False, "volume_low": None, "volume_high": None, "difficulty": 5},
            {"id": m3, "keyword": "job board india", "volume": None, "volume_is_range": True, "volume_low": 1000, "volume_high": 10000, "difficulty": 55},
        ],
        "raw_gsc_performance": [{"query": "hiring platform", "page": "https://korum.worldhire.com/", "impressions": 500}],
        "keywords": [], "critical_rules": [{"url_pattern": r"^/(dashboard|recruiter|admin|api)/", "assertion": "must_noindex"}],
        "raw_crawl_pages": [{"url": "https://korum.worldhire.com/", "status_code": 200}, {"url": "https://korum.worldhire.com/recruiter/inbox", "status_code": 200}],
    }
    llm = FakeLLM({"keyword": lambda s, u, sc: {"assignments": [
        {"keyword": "hiring platform", "intent": "commercial", "cluster": "platform", "mapped_url": "https://korum.worldhire.com/"},
        {"keyword": "recruiter inbox login", "intent": "navigational", "mapped_url": "https://korum.worldhire.com/recruiter/inbox"},
        {"keyword": "job board india", "intent": "commercial", "mapped_url": "https://korum.worldhire.com/made-up"},
    ]}})
    out = run_keyword(AnalystContext(llm=llm, model="fake"), AnalystInput(project=project, run_id=uuid.uuid4(), rows=rows, params={"reserved_keywords": []}))
    by = {k.keyword: k for k in out.keywords}
    assert by["hiring platform"].volume == 1200 and by["hiring platform"].mapped_url == "https://korum.worldhire.com/"
    assert by["recruiter inbox login"].blocked_for_index and by["recruiter inbox login"].mapped_url is None
    assert by["job board india"].volume is None and by["job board india"].volume_is_range and by["job board india"].volume_low == 1000
    assert by["job board india"].mapped_url is None, "a url the crawl never saw is not a mapping"
    assert all(k.evidence_ref in (m1, m2, m3) for k in out.keywords)


def test_content_strips_unbacked_statistics_and_enforces_cap(worker_url, seeded):
    project = _project(worker_url, seeded)
    d1 = uuid.uuid4()
    rows = {
        "keywords": [{"keyword": "hiring platform"}], "content_briefs": [], "raw_crawl_pages": [{"url": "https://korum.worldhire.com/about", "status_code": 200}],
        "raw_fetched_documents": [{"id": d1, "url": "https://r.example", "content_text": "Median time to hire fell to 31 days in the survey of 4,200 employers."}],
        "critical_rules": [],
    }
    llm = FakeLLM({"content": lambda s, u, sc: {
        "title": "Hiring faster", "answer_block": "Time to hire is 31 days on average. Some say 20 days.", "outline": ["a"],
        "draft": "Median time to hire fell to 31 days. Another study found 45% of teams hire remotely. Hiring is changing.",
        "sources": [{"claim": "Median time to hire fell to 31 days.", "fetched_doc_id": str(d1)},
                    {"claim": "45% of teams hire remotely.", "fetched_doc_id": str(d1)}],
        "internal_links": ["https://korum.worldhire.com/about", "https://korum.worldhire.com/recruiter/inbox"]}})
    out = run_content(AnalystContext(llm=llm, model="fake"), AnalystInput(project=project, run_id=uuid.uuid4(), rows=rows, params={"keyword": "hiring platform"}))
    assert len(out.sources) == 1 and out.sources[0].fetched_doc_id == d1
    assert "45%" not in out.draft and "31 days" in out.draft and "Hiring is changing." in out.draft
    assert "20 days" not in out.answer_block
    assert out.internal_links == ["https://korum.worldhire.com/about"]
    assert strip_unbacked_numbers("No numbers here. 12 apples.", set()) == "No numbers here."
    rows["content_briefs"] = [{"created_at": datetime.now(UTC)} for _ in range(project.monthly_content_cap)]
    with pytest.raises(ContentCapReached):
        run_content(AnalystContext(llm=llm, model="fake"), AnalystInput(project=project, run_id=uuid.uuid4(), rows=rows, params={"keyword": "x"}))
    assert len(llm.calls) == 1, "the cap is checked before any model call"


def test_offpage_cap_is_enforced_in_code(worker_url, seeded):
    project = _project(worker_url, seeded)
    serp = [{"id": uuid.uuid4(), "query": f"q{i}", "results": [{"rank": 1, "domain": f"outlet{i}.example"}], "ai_overview_citations": []} for i in range(80)]
    llm = FakeLLM({"offpage": lambda s, u, sc: {"pitches": [{"outlet_domain": f"outlet{i}.example", "subject": "Data on hiring", "body": "We have a dataset to share."} for i in range(80)]}})
    out = run_offpage(AnalystContext(llm=llm, model="fake"), AnalystInput(project=project, run_id=uuid.uuid4(), rows={"raw_serp": serp, "mentions": []}))
    assert len(out.pitches) == 50
    out = run_offpage(AnalystContext(llm=llm, model="fake"), AnalystInput(project=project, run_id=uuid.uuid4(), rows={"raw_serp": serp, "mentions": []}, params={"batch_cap": 5}))
    assert len(out.pitches) == 5


def test_onpage_candidates_skip_protected_paths(worker_url, seeded):
    project = _project(worker_url, seeded)
    rows = {"raw_crawl_pages": [
        {"id": uuid.uuid4(), "url": "https://korum.worldhire.com/about", "status_code": 200, "title": "About", "meta_description": None, "h1": ["About"]},
        {"id": uuid.uuid4(), "url": "https://korum.worldhire.com/recruiter/inbox", "status_code": 200, "title": None, "meta_description": None, "h1": []},
    ], "keywords": [], "brand_rules": [], "critical_rules": [{"url_pattern": r"^/(dashboard|recruiter|admin|api)/", "assertion": "must_noindex"}]}
    c = candidates(AnalystInput(project=project, run_id=uuid.uuid4(), rows=rows))
    assert [x["url"] for x in c] == ["https://korum.worldhire.com/about"] and "title" in c[0]["needs"] and "meta_description" in c[0]["needs"]


def test_ecommerce_detects_schema_and_price_problems(worker_url, seeded):
    project = _project(worker_url, seeded, "rejuveluxe")
    pid = uuid.uuid4()
    rows = {"critical_rules": [
        {"rule_key": "product_price_matches_schema", "url_pattern": r"^/products/", "assertion": "schema_matches_visible", "active": True},
        {"rule_key": "variant_canonical", "url_pattern": r"^/products/.*\?variant=", "assertion": "must_canonical_to_parent", "active": True},
    ], "raw_crawl_pages": [
        {"id": pid, "url": "https://rejuveluxe.com/products/serum?variant=50ml", "status_code": 200, "canonical": None, "visible_price": "$59.00",
         "raw_jsonld": [{"@type": "Product", "name": "Serum", "offers": {"@type": "Offer", "price": "49.00", "availability": "https://schema.org/OutOfStock"}}]},
    ]}
    found = detect_ecom(AnalystInput(project=project, run_id=uuid.uuid4(), rows=rows))
    types = sorted(f["issue_type"] for f in found)
    assert types == ["offer_schema_incomplete", "out_of_stock_without_handling", "product_price_matches_schema", "variant_canonical"]
    assert all(f["evidence_ref"] == pid for f in found)


def test_content_pipeline_end_to_end_with_stage2(worker_url, seeded):
    project = _project(worker_url, seeded, "worldhire")
    site = FakeSite()

    def content(system, user, schema):
        import re
        doc_id = re.search(r"<<<UNTRUSTED_DOCUMENT id=(\S+)", user).group(1)
        return {"title": "About WorldHire", "answer_block": "Words words words.", "outline": ["a"],
                "draft": "Words words words. Hiring globally takes 90 days on average. Teams adapt.",
                "sources": [{"claim": "Words words words.", "fetched_doc_id": doc_id}, {"claim": "Hiring globally takes 90 days on average.", "fetched_doc_id": doc_id}],
                "internal_links": []}

    def verifier(system, user, schema):
        import json
        claims = json.loads(user.split("CLAIMS:\n", 1)[1].split("\n\nDOCUMENTS:\n", 1)[0])
        return {"verdicts": [{"index": c["index"], "verdict": "verified", "supporting_span": "Words words words."} for c in claims], "suspicious_content": []}

    rt = Runtime(db_url=worker_url, model="fake", collector_overrides={"*": {"_transport": site.transport}},
                 llm=FakeLLM({"content": content, "gate_stage2": verifier}))
    h = run_workflow(rt, project, "content_pipeline", "manual", f"content:{uuid.uuid4().hex[:6]}",
                     {"keyword": "global hiring", "source_urls": ["https://korum.worldhire.com/about"]})
    assert h.status == "paused_for_approval"
    with project_scope(project.id, url=worker_url) as s:
        brief = s.fetchone("select draft, sources, gate_result from content_briefs where project_id = %(project_id)s and run_id = %(id)s", {"id": h.id})
        g2 = s.fetchone("select stage2_verdict, claims_verified, claims_cut from gate_results where project_id = %(project_id)s and run_id = %(id)s and stage2_verdict is not null", {"id": h.id})
        appr = s.fetchone("select action_type, reversal_payload from approvals where project_id = %(project_id)s and run_id = %(id)s", {"id": h.id})
    # the analyst already dropped the unbacked "90 days" claim in code; stage 2 then verifies the survivor
    assert g2["stage2_verdict"] == "pass" and g2["claims_verified"] == 1 and g2["claims_cut"] == 0
    assert "90 days" not in brief["draft"] and len(brief["sources"]) == 1 and brief["gate_result"] == "stage2_pass"
    assert appr["action_type"] == "publish_content" and appr["reversal_payload"]["kind"] == "unpublish"


def test_quarterly_keyword_end_to_end(worker_url, seeded):
    project = _project(worker_url, seeded, "korum")

    def dfs(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tasks": [{"status_code": 20000, "result": [
            {"keyword": "hiring platform india", "search_volume": 1300, "cpc": 1.2, "keyword_difficulty": 41},
            {"keyword": "recruiter software", "search_volume": {"min": 100, "max": 1000}},
        ]}]})

    llm = FakeLLM({"keyword": lambda s, u, sc: {"assignments": [{"keyword": "hiring platform india", "intent": "commercial", "cluster": "platform", "mapped_url": None}]}})
    rt = Runtime(db_url=worker_url, model="fake", llm=llm, collector_overrides={"keyword_metrics": {"_transport": httpx.MockTransport(dfs)}})
    h = run_workflow(rt, project, "quarterly_keyword", "cron", f"quarter:test-{uuid.uuid4().hex[:6]}", {"keywords": ["hiring platform india", "recruiter software", "job board india"]})
    assert h.status == "done"
    with project_scope(project.id, url=worker_url) as s:
        kws = s.fetchall("select keyword, intent, latest_metric_id from keywords where project_id = %(project_id)s order by keyword")
        metrics = s.fetchall("select keyword, volume, volume_is_range, volume_low from raw_keyword_metrics where project_id = %(project_id)s and run_id = %(id)s order by keyword", {"id": h.id})
        gaps = s.fetchall("select affected_scope from collection_gaps where project_id = %(project_id)s and run_id = %(id)s", {"id": h.id})
    assert {k["keyword"] for k in kws} >= {"hiring platform india", "recruiter software"}
    rs = next(m for m in metrics if m["keyword"] == "recruiter software")
    assert rs["volume"] is None and rs["volume_is_range"] and rs["volume_low"] == 100
    assert any(g["affected_scope"] == "job board india" for g in gaps), "a keyword with no metric is a gap, not a zero"


def test_nested_jsonld_types_of_an_allowed_parent_are_not_disallowed():
    """Production run b056869d: 46 'critical' issues flagged Question, Answer and ListItem on korum,
    which are the children FAQPage and BreadcrumbList are made of."""
    from analysts.technical import allowed_schema_types

    allowed = allowed_schema_types(["Organization", "WebSite", "Article", "FAQPage", "BreadcrumbList"])
    assert {"Question", "Answer", "ListItem", "SearchAction"} <= allowed
    assert "JobPosting" not in allowed and "Product" not in allowed
    assert allowed_schema_types(None) == set(), "no declared list means the check is off"
