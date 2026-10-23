"""PageSpeed Insights: what the vitals collector stores, which pages it measures, and how vitals issues close."""
import uuid

import httpx
import pytest

from collectors.psi import field_row, lab_row, top_fixes
from collectors.registry import collect
from contracts.project import Project, ProjectConfig
from db.connection import project_scope
from orchestrator.graphs.page_speed import pick_urls, vitals_urls
from tests.conftest import requires_db

HOME = "https://korum.worldhire.com/"


def _psi(lcp=3200.0, score=0.62, field: dict | None = None, origin_fallback=False, origin: dict | None = None, error=None) -> dict:
    """A runPagespeed response with the fields the collector reads."""
    lh = {
        "categories": {"performance": {"score": score}},
        "audits": {
            "largest-contentful-paint": {"score": 0.4, "numericValue": lcp},
            "first-contentful-paint": {"score": 0.7, "numericValue": 1800.4},
            "total-blocking-time": {"score": 0.5, "numericValue": 420.0},
            "cumulative-layout-shift": {"score": 0.9, "numericValue": 0.05},
            "server-response-time": {"score": 1, "numericValue": 310.0},
            "render-blocking-resources": {"title": "Eliminate render-blocking resources", "score": 0.3, "displayValue": "Potential savings of 900 ms",
                                          "details": {"type": "opportunity", "overallSavingsMs": 900}},
            "unused-javascript": {"title": "Reduce unused JavaScript", "score": 0.5, "metricSavings": {"LCP": 450, "FCP": 0}},
            "legacy-javascript": {"title": "Avoid serving legacy JavaScript", "score": 0.5, "metricSavings": {"TBT": 1200}},
            "layout-shift-elements": {"title": "Avoid large layout shifts", "score": 0, "metricSavings": {"CLS": 0.2}},
            "uses-text-compression": {"title": "Enable text compression", "score": 1, "details": {"overallSavingsMs": 300}},
            "font-display": {"title": "Ensure text remains visible", "score": None, "metricSavings": {"FCP": 100}},
        },
    }
    if error:
        lh = {"runtimeError": {"code": error, "message": "boom"}}
    out: dict = {"lighthouseResult": lh}
    if field is not None:
        out["loadingExperience"] = {"id": HOME, "metrics": field, "overall_category": "SLOW", **({"origin_fallback": True} if origin_fallback else {})}
    if origin is not None:
        out["originLoadingExperience"] = {"id": "https://korum.worldhire.com", "metrics": origin, "overall_category": "AVERAGE"}
    return out


FIELD = {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2900}, "INTERACTION_TO_NEXT_PAINT": {"percentile": 260},
         "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 12}, "FIRST_CONTENTFUL_PAINT_MS": {"percentile": 1500},
         "EXPERIMENTAL_TIME_TO_FIRST_BYTE": {"percentile": 700}}
ORIGIN = {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2100}, "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 3}}


def _project(domains=("korum.worldhire.com",)) -> Project:
    return Project(id=uuid.uuid4(), slug="t", display_name="t", domains=list(domains), vertical="recruitment",
                   approver_id=uuid.uuid4(), monthly_cost_cap_usd=10, enabled_agents=[])


def test_lab_row_reads_lighthouse_and_ranks_the_fixes():
    row = lab_row(_psi())
    assert row["source"] == "lab" and row["performance_score"] == 62
    assert (row["lcp_ms"], row["fcp_ms"], row["tbt_ms"], row["ttfb_ms"], row["cls"]) == (3200.0, 1800.4, 420.0, 310.0, 0.05)
    assert row["inp_ms"] is None, "a page-load run has no interaction"
    fixes = row["opportunities"].obj
    assert [f["id"] for f in fixes] == ["legacy-javascript", "render-blocking-resources", "unused-javascript"], \
        "largest saving first; passing, unscored, CLS-only and metric audits left out"
    assert fixes[1] == {"id": "render-blocking-resources", "title": "Eliminate render-blocking resources",
                        "display_value": "Potential savings of 900 ms", "savings_ms": 900}
    assert len(top_fixes(_psi()["lighthouseResult"]["audits"], n=1)) == 1


def test_field_row_scales_cls_and_keeps_the_category():
    row = field_row({"metrics": FIELD, "overall_category": "SLOW"})
    assert row == {"source": "field", "lcp_ms": 2900.0, "inp_ms": 260.0, "cls": 0.12, "fcp_ms": 1500.0, "ttfb_ms": 700.0, "field_category": "SLOW"}


def test_pick_urls_homepage_then_changed_then_most_clicked():
    project = _project(("korum.worldhire.com",))
    protected = ["^/(dashboard|recruiter|admin|api)(/|$)"]
    top = ["https://korum.worldhire.com/jobs", "https://other.example/x", "https://korum.worldhire.com/recruiter/inbox",
           "https://korum.worldhire.com/about#team", "https://www.korum.worldhire.com/pricing", "https://korum.worldhire.com/blog"]
    got = pick_urls(project, top, protected, limit=5, first=["/about", "/admin", "https://korum.worldhire.com/"])
    assert got == [HOME, "https://korum.worldhire.com/about", "https://korum.worldhire.com/jobs",
                   "https://www.korum.worldhire.com/pricing", "https://korum.worldhire.com/blog"]
    assert pick_urls(project, top, protected, limit=1) == [HOME]


def test_vitals_page_limit_is_bounded_and_defaults_to_ten():
    base = {"slug": "x", "vertical": "saas", "domains": ["x.com"], "approver_id": str(uuid.uuid4())}
    assert ProjectConfig.model_validate(base).vitals_page_limit == 10
    for bad in (0, 26):
        with pytest.raises(ValueError):
            ProjectConfig.model_validate({**base, "vitals_page_limit": bad})


def test_technical_explains_a_large_audit_in_batches():
    """Run 52d25e67: 703 issues from 150 job pages went to the model in one call and the reply was cut off mid-JSON."""
    import math

    from analysts import technical
    from analysts.base import AnalystContext, AnalystInput
    from llm.fake import FakeLLM
    from tests.test_orchestrator_m4 import explain

    pages = [{"id": uuid.uuid4(), "url": f"https://korum.worldhire.com/jobs/{n}", "status_code": 200, "word_count": 400,
              "title": f"Job {n}", "h1": [], "meta_description": "d", "canonical": f"https://korum.worldhire.com/jobs/{n}"} for n in range(95)]
    rows = {"raw_crawl_pages": pages, "raw_vitals": [], "raw_sitemap_urls": [], "critical_rules": [], "raw_url_inspection": []}
    inp = AnalystInput(project=_project(), run_id=uuid.uuid4(), rows=rows)
    llm = FakeLLM({"technical": explain})
    report = technical.run(AnalystContext(llm=llm, model="fake"), inp)
    explained = [d for d in technical.detect(inp) if d.issue_type not in technical.INDEX_ISSUE_TYPES]
    assert len(explained) >= 95
    assert len(llm.calls) == math.ceil(len(explained) / technical.EXPLAIN_BATCH) > 1
    assert all(i.recommended_fix == f"Fix {i.issue_type}." for i in report.issues), "every issue got its own batch's explanation"


def test_page_speed_is_a_free_registered_workflow():
    from orchestrator import budget
    from orchestrator.runner import WORKFLOWS, _load_workflows

    _load_workflows()
    assert "page_speed" in WORKFLOWS
    assert budget.DEFAULT_ESTIMATE_USD["page_speed"] == 0.0, "PageSpeed Insights is free; the 5 USD fallback would block runs"


# ---- against Postgres ----

def _seeded_project(worker_url, seeded, slug="korum") -> Project:
    with project_scope(seeded[slug], url=worker_url) as s:
        return Project.from_row(s.fetchone("select * from projects where id = %(project_id)s"))


def _rows(worker_url, project_id, run_id, table="raw_vitals"):
    with project_scope(project_id, url=worker_url) as s:
        return s.fetchall(f"select * from {table} where project_id = %(project_id)s and run_id = %(r)s", {"r": run_id})


@requires_db
@pytest.mark.db
def test_collector_stores_page_field_data_and_the_origin_once(worker_url, seeded):
    project = _seeded_project(worker_url, seeded)
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        url = request.url.params["url"]
        calls.append((url, request.url.params["strategy"]))
        if url.endswith("/busy"):
            return httpx.Response(200, json=_psi(field=FIELD, origin=ORIGIN))
        return httpx.Response(200, json=_psi(lcp=1200, field=ORIGIN, origin_fallback=True, origin=ORIGIN))

    run_id = uuid.uuid4()
    urls = [HOME, "https://korum.worldhire.com/busy"]
    res = collect("vitals", project, run_id, {"_transport": httpx.MockTransport(handle), "urls": urls}, db_url=worker_url)
    assert len(calls) == 4 and {s for _, s in calls} == {"mobile", "desktop"}
    rows = _rows(worker_url, project.id, run_id)
    by = {(r["url"], r["strategy"], r["source"], r["field_scope"]): r for r in rows}
    assert len(rows) == 8, "4 lab rows, 2 page field rows for /busy, 1 origin row per strategy"
    assert all((u, s, "lab", None) in by for u in urls for s in ("mobile", "desktop"))
    assert by[(urls[1], "mobile", "field", "url")]["field_category"] == "SLOW"
    assert float(by[(urls[1], "mobile", "field", "url")]["cls"]) == 0.12
    origin = [r for r in rows if r["field_scope"] == "origin"]
    assert sorted(r["strategy"] for r in origin) == ["desktop", "mobile"] and all(r["url"] == "https://korum.worldhire.com" for r in origin)
    assert float(by[(HOME, "mobile", "lab", None)]["performance_score"]) == 62
    assert by[(HOME, "mobile", "lab", None)]["opportunities"][0]["id"] == "legacy-javascript"
    assert not any(r["url"] == HOME and r["source"] == "field" for r in rows), "origin numbers are never stored as the homepage's own"
    gaps = _rows(worker_url, project.id, run_id, "collection_gaps")
    assert res.gaps == 2 and all(g["reason"] == "no field data (CrUX) for url" and g["affected_scope"].startswith(HOME) for g in gaps)


@requires_db
@pytest.mark.db
def test_collector_failures_are_gaps(worker_url, seeded):
    project = _seeded_project(worker_url, seeded)
    run_id = uuid.uuid4()
    res = collect("vitals", project, run_id, {}, db_url=worker_url)
    assert res.rows_written == 0 and [g["reason"] for g in _rows(worker_url, project.id, run_id, "collection_gaps")] == ["PAGESPEED_API_KEY not set"]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params["strategy"] == "mobile":
            return httpx.Response(429, json={"error": {"code": 429}})
        return httpx.Response(200, json=_psi(error="FAILED_DOCUMENT_REQUEST"))

    run_id = uuid.uuid4()
    res = collect("vitals", project, run_id, {"_transport": httpx.MockTransport(handle), "urls": [HOME]}, db_url=worker_url)
    reasons = sorted(g["reason"] for g in _rows(worker_url, project.id, run_id, "collection_gaps"))
    assert res.rows_written == 0 and res.partial
    assert reasons == ["Lighthouse could not measure the page: FAILED_DOCUMENT_REQUEST", "no field data (CrUX) for url", "runPagespeed returned 429"]


@requires_db
@pytest.mark.db
def test_vitals_urls_follow_search_console_clicks_and_skip_protected_routes(worker_url, seeded):
    project = _seeded_project(worker_url, seeded)
    run_id = uuid.uuid4()
    with project_scope(project.id, caller="collector:gsc_performance", url=worker_url) as s:
        for page, clicks in (("https://korum.worldhire.com/jobs", 40), ("https://korum.worldhire.com/jobs", 30),
                             ("https://korum.worldhire.com/recruiter/inbox", 500), ("https://korum.worldhire.com/about", 50)):
            s.insert("raw_gsc_performance", {"run_id": run_id, "date": "today", "page": page, "clicks": clicks})
        s.insert("raw_gsc_performance", {"run_id": run_id, "date": "2020-01-01", "page": "https://korum.worldhire.com/old", "clicks": 9999})
    sitemap_run = uuid.uuid4()   # other tests' crawls leave their own sitemap behind; this one is the latest
    with project_scope(project.id, caller="collector:site_crawl", url=worker_url) as s:
        for page in ("jobs", "about", "recruiter/inbox", "old"):
            s.insert("raw_sitemap_urls", {"run_id": sitemap_run, "sitemap_url": HOME + "sitemap.xml", "url": f"https://korum.worldhire.com/{page}"})
    with project_scope(project.id, url=worker_url) as s:
        urls = vitals_urls(s, project, 10)
    assert urls[:3] == [HOME, "https://korum.worldhire.com/jobs", "https://korum.worldhire.com/about"]
    assert not any("/recruiter/" in u or u.endswith("/old") for u in urls)

    # Run 52d25e67: Search Console still had clicks for pages that now redirect off the site. Once a sitemap has
    # been read, only Search Console pages it lists are measured; the homepage always is.
    with project_scope(project.id, caller="collector:site_crawl", url=worker_url) as s:
        s.insert("raw_sitemap_urls", {"run_id": uuid.uuid4(), "sitemap_url": HOME + "sitemap.xml", "url": "https://korum.worldhire.com/jobs"})
    with project_scope(project.id, url=worker_url) as s:
        urls = vitals_urls(s, project, 10)
    assert urls == [HOME, "https://korum.worldhire.com/jobs"], "/about is not in the latest sitemap"


@requires_db
@pytest.mark.db
def test_vitals_issue_closes_only_when_the_same_measurement_is_back_under(worker_url, seeded):
    from orchestrator.gate_runner import resolve_unseen_issues, write_issues

    project = _seeded_project(worker_url, seeded)
    url = f"https://korum.worldhire.com/speed-{uuid.uuid4().hex[:6]}"
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
    with project_scope(project.id, caller="collector:vitals", url=worker_url) as s:
        lab_a = s.insert("raw_vitals", {"run_id": run_a, "url": url, "strategy": "mobile", "source": "lab", "lcp_ms": 4100})
        field_a = s.insert("raw_vitals", {"run_id": run_a, "url": url, "strategy": "mobile", "source": "field", "field_scope": "url", "inp_ms": 400})
        cls_a = s.insert("raw_vitals", {"run_id": run_a, "url": url, "strategy": "desktop", "source": "lab", "cls": 0.3})
        # run b measured mobile again: lab LCP is fine now, field INP has no value; desktop was not measured
        s.insert("raw_vitals", {"run_id": run_b, "url": url, "strategy": "mobile", "source": "lab", "lcp_ms": 1900})
        s.insert("raw_vitals", {"run_id": run_b, "url": url, "strategy": "mobile", "source": "field", "field_scope": "url", "inp_ms": None})

    def issue(t, ref):
        return {"issue_type": t, "severity": "high", "url": url, "evidence": "e", "evidence_ref": str(ref)}

    with project_scope(project.id, url=worker_url) as s:
        write_issues(s, run_a, {"issues": [issue("lcp_slow", lab_a), issue("inp_slow", field_a), issue("cls_high", cls_a)]})
        n = resolve_unseen_issues(s, run_b, [{"issues": []}])
        status = {r["issue_type"]: r["status"] for r in s.fetchall("select issue_type, status from issues where project_id = %(project_id)s and url = %(u)s", {"u": url})}
    assert n == 1
    assert status == {"lcp_slow": "resolved", "inp_slow": "open", "cls_high": "open"}

    run_c = uuid.uuid4()
    with project_scope(project.id, caller="collector:vitals", url=worker_url) as s:
        cls_c = s.insert("raw_vitals", {"run_id": run_c, "url": url, "strategy": "desktop", "source": "lab", "cls": 0.25})
    with project_scope(project.id, url=worker_url) as s:
        resolve_unseen_issues(s, run_c, [{"issues": [issue("cls_high", cls_c)]}])
        still = s.fetchone("select status from issues where project_id = %(project_id)s and url = %(u)s and issue_type = 'cls_high'", {"u": url})
    assert still["status"] == "open", "flagged again in this run, so not resolved"


@requires_db
@pytest.mark.db
def test_post_deploy_audit_measures_vitals(worker_url, seeded):
    from llm.fake import FakeLLM
    from orchestrator.runner import run_workflow
    from orchestrator.runtime import Runtime
    from tests.fakesite import FakeSite
    from tests.test_orchestrator_m4 import explain

    rt = Runtime(db_url=worker_url, llm=FakeLLM({"technical": explain}), model="fake-model",
                 collector_overrides={"*": {"_transport": FakeSite().transport}})
    project = _seeded_project(worker_url, seeded)
    h = run_workflow(rt, project, "post_deploy_audit", "test", f"deploy:{uuid.uuid4().hex[:8]}", {"changed_urls": ["/about", "/recruiter/inbox"]})
    assert h.status in ("done", "paused_for_approval")
    with project_scope(project.id, url=worker_url) as s:
        log = s.fetchone("select status from agent_logs where project_id = %(project_id)s and run_id = %(r)s and agent = 'vitals'", {"r": h.id})
        scopes = [g["affected_scope"] for g in s.fetchall("select affected_scope from collection_gaps where project_id = %(project_id)s and run_id = %(r)s and collector = 'vitals'", {"r": h.id})]
    assert log is not None, "the deploy audit runs the vitals collector"
    assert any(sc.startswith("https://korum.worldhire.com/about ") for sc in scopes), "changed pages are measured"
    assert not any("/recruiter/" in sc for sc in scopes), "protected routes are not"
