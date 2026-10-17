"""Competitor overview (DataForSEO Labs), backlink changes and daily rank tracking."""
import json
import uuid
from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import pytest

from collectors.registry import collect
from contracts.project import Project
from db.connection import project_scope
from orchestrator.scheduler import due
from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


def _project(worker_url, seeded, slug="korum") -> Project:
    with project_scope(seeded[slug], url=worker_url) as s:
        return Project.from_row(s.fetchone("select * from projects where id = %(project_id)s"))


def _task(result: dict) -> dict:
    return {"status_code": 20000, "cost": 0.01, "tasks": [{"status_code": 20000, "result": [result]}]}


def _organic(etv, count):
    return {"organic": {"etv": etv, "count": count, "pos_1": 3, "pos_2_3": 5, "pos_4_10": 20, "pos_11_20": 40, "pos_21_30": 10, "pos_91_100": 2,
                        "is_new": 7, "is_up": 9, "is_down": 4, "is_lost": 6}, "paid": {"etv": 12}}


def test_competitor_overview_collects_labs_metrics_history_and_top_pages(worker_url, seeded):
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)[0]
        endpoint = request.url.path.rsplit("/", 2)[-2]
        calls.append((endpoint, body["target"]))
        if body["target"] == "korum.worldhire.com":
            return httpx.Response(200, json=_task({"items": None}))      # a new site Labs knows nothing about
        if endpoint == "domain_rank_overview":
            return httpx.Response(200, json=_task({"items": [{"metrics": _organic(41000.5, 3200)}]}))
        if endpoint == "historical_rank_overview":
            return httpx.Response(200, json=_task({"items": [{"year": 2026, "month": 9, "metrics": _organic(39000, 3100)},
                                                             {"year": 2026, "month": 13, "metrics": _organic(1, 1)}]}))
        return httpx.Response(200, json=_task({"items": [{"page_address": "https://www.naukri.com/jobs", "metrics": _organic(9000, 400)},
                                                         {"metrics": _organic(1, 1)}]}))

    project = _project(worker_url, seeded).model_copy(update={"competitors": ["www.naukri.com"]})
    run_id = uuid.uuid4()
    res = collect("competitor_overview", project, run_id, {"_transport": httpx.MockTransport(handle), "today": date(2026, 10, 17)}, db_url=worker_url)
    assert calls == [("domain_rank_overview", "korum.worldhire.com"), ("domain_rank_overview", "naukri.com"),
                     ("historical_rank_overview", "naukri.com"), ("relevant_pages", "naukri.com")], "no history or pages for a site with no data"
    assert res.gaps == 1 and res.rows_written == 3
    with project_scope(project.id, url=worker_url) as s:
        ov = s.fetchone("select * from raw_domain_overview where project_id = %(project_id)s and run_id = %(r)s", {"r": run_id})
        hist = s.fetchall("select month, etv from raw_domain_history where project_id = %(project_id)s and run_id = %(r)s", {"r": run_id})
        page = s.fetchone("select page, etv, keywords from raw_domain_pages where project_id = %(project_id)s and run_id = %(r)s", {"r": run_id})
        gap = s.fetchone("select reason, affected_scope from collection_gaps where project_id = %(project_id)s and run_id = %(r)s", {"r": run_id})
    assert (ov["target"], ov["role"], float(ov["etv"]), ov["keywords"], ov["pos_21_100"], ov["is_new"], ov["is_lost"]) == ("naukri.com", "competitor", 41000.5, 3200, 12, 7, 6)
    assert [(h["month"], float(h["etv"])) for h in hist] == [(date(2026, 9, 1), 39000.0)], "a month that is not a month is dropped"
    assert (page["page"], float(page["etv"]), page["keywords"]) == ("https://www.naukri.com/jobs", 9000.0, 400)
    assert gap["affected_scope"] == "korum.worldhire.com" and gap["reason"].startswith("DataForSEO Labs has no organic data")


def test_backlink_changes_and_lost_links_are_collected(worker_url, seeded):
    bodies: dict[str, list[dict]] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)[0]
        endpoint = request.url.path.rsplit("/", 2)[-2]
        bodies.setdefault(endpoint, []).append(body)
        if endpoint == "timeseries_new_lost_summary":
            return httpx.Response(200, json=_task({"items": [
                {"date": "2026-09-01 00:00:00 +00:00", "new_referring_domains": 4, "lost_referring_domains": 1, "new_backlinks": 9, "lost_backlinks": 2},
                {"date": None, "new_referring_domains": 99}]}))
        if endpoint == "referring_domains" and body["target"] == "korum.worldhire.com":
            return httpx.Response(200, json=_task({"items": [
                {"domain": "producthunt.com", "rank": 70, "backlinks_spam_score": 3, "first_seen": "2026-10-01 00:00:00 +00:00"},
                {"domain": "oldblog.example", "rank": 18, "backlinks_spam_score": 44, "lost_date": "2026-10-08 00:00:00 +00:00"}]}))
        return httpx.Response(200, json=_task({"items": []}))

    project = _project(worker_url, seeded).model_copy(update={"competitors": ["naukri.com"]})
    run_id = uuid.uuid4()
    collect("backlink_gap", project, run_id, {"_transport": httpx.MockTransport(handle), "today": date(2026, 10, 17)}, db_url=worker_url)
    status = {b["target"]: b["backlinks_status_type"] for b in bodies["referring_domains"]}
    assert status == {"naukri.com": "live", "korum.worldhire.com": "all"}, "the site's lost links are kept; competitors' are live only"
    assert [b["date_from"] for b in bodies["timeseries_new_lost_summary"]] == ["2025-10-01", "2025-10-01"]
    with project_scope(project.id, url=worker_url) as s:
        changes = s.fetchall("select target, month, new_referring_domains, lost_referring_domains from raw_backlink_changes where project_id = %(project_id)s and run_id = %(r)s order by target", {"r": run_id})
        lost = s.fetchone("select domain, lost_date from raw_referring_domains where project_id = %(project_id)s and run_id = %(r)s and lost_date is not null", {"r": run_id})
    assert [(c["target"], c["month"], c["new_referring_domains"], c["lost_referring_domains"]) for c in changes] == [
        ("korum.worldhire.com", date(2026, 9, 1), 4, 1), ("naukri.com", date(2026, 9, 1), 4, 1)]
    assert lost["domain"] == "oldblog.example"


def _set_limit(db_url, project_id, n):
    import psycopg

    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("update projects set rank_tracking_limit = %s where id = %s", (n, project_id))


def test_daily_rank_checks_the_top_tracked_keywords_and_only_for_projects_that_opt_in(db_url, worker_url, seeded):
    from orchestrator.graphs.competitive import top_keywords
    from orchestrator.runner import run_workflow
    from orchestrator.runtime import Runtime

    project = _project(worker_url, seeded)
    assert project.rank_tracking_limit == 25, "seeded from config/projects/korum.yaml"
    with project_scope(project.id, caller="collector:keyword_metrics", url=worker_url) as s:
        mid = s.insert("raw_keyword_metrics", {"run_id": uuid.uuid4(), "keyword": "zz high volume", "volume": 9000})
    with project_scope(project.id, url=worker_url) as s:
        s.execute("delete from keywords where project_id = %(project_id)s")
        for kw, mapped, metric, blocked in [("aa low", None, None, False), ("zz high volume", None, mid, False),
                                            ("mm mapped", "https://korum.worldhire.com/jobs", None, False), ("recruiter login", None, None, True)]:
            s.insert("keywords", {"keyword": kw, "mapped_url": mapped, "latest_metric_id": metric, "blocked_for_index": blocked})
        assert top_keywords(s, 2) == ["mm mapped", "zz high volume"], "mapped first, then by volume; blocked never"

    asked = []

    def serp(request: httpx.Request) -> httpx.Response:
        asked.append(json.loads(request.content)[0]["keyword"])
        return httpx.Response(200, json=_task({"items": [{"type": "organic", "rank_absolute": 4, "domain": "korum.worldhire.com", "url": "https://korum.worldhire.com/jobs"}]}))

    rt = Runtime(db_url=worker_url, model="fake", collector_overrides={"serp": {"_transport": httpx.MockTransport(serp)}})
    _set_limit(db_url, project.id, 2)
    h = run_workflow(rt, project, "daily_rank", "test", f"rank-{uuid.uuid4().hex[:6]}")
    assert h.status == "done" and asked == ["mm mapped", "zz high volume"]
    with project_scope(project.id, url=worker_url) as s:
        assert s.fetchone("select count(*) as n from raw_serp where project_id = %(project_id)s and run_id = %(r)s", {"r": h.id})["n"] == 2

    asked.clear()
    _set_limit(db_url, project.id, 0)
    h = run_workflow(rt, project, "daily_rank", "test", f"rank-{uuid.uuid4().hex[:6]}")
    assert h.status == "done" and asked == [], "an empty list must not fall back to every tracked keyword"


def test_daily_rank_is_scheduled_at_0615_ist():
    ist = timezone(timedelta(hours=5, minutes=30))
    at = datetime(2026, 10, 17, 6, 15, tzinfo=ist).astimezone(UTC)
    projects = [{"slug": "a"}]
    assert [w for _, w, _ in due(at, projects)] == ["daily_rank"]
