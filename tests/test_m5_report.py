"""M5 acceptance: a report generated during a simulated Search Console outage names the gap in caveats
and interpolates nothing. Plus trend analyst, digest, approvals decisions."""
import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from analysts.base import AnalystContext, AnalystInput
from analysts.report import compute_metrics, foreign_numbers
from analysts.report import run as run_report
from analysts.trend import detect as detect_trends
from contracts.project import Project
from db.connection import project_scope
from llm.fake import FakeLLM
from orchestrator import approvals, notify
from orchestrator.runner import run_workflow
from orchestrator.runtime import Runtime
from orchestrator.scheduler import IST, due
from tests.conftest import requires_db
from tests.fakesite import FakeSite

pytestmark = [requires_db, pytest.mark.db]


def _project(worker_url, seeded, slug="korum") -> Project:
    with project_scope(seeded[slug], url=worker_url) as s:
        return Project.from_row(s.fetchone("select * from projects where id = %(project_id)s"))


def commentary_from_metrics(system, user, schema):
    payload = json.loads(user.split("\n\nYour previous", 1)[0])
    parts = [f"Report cutoff {payload['cutoff_date']}."]
    for m in payload["metrics"]:
        if m["value"] is None:
            parts.append(f"{m['name']} is unavailable for this period because of a collection gap.")
        else:
            parts.append(f"{m['name']} was {m['value']:g}.")
    return {"commentary": " ".join(parts)}


def test_report_during_gsc_outage_names_gap_and_interpolates_nothing(worker_url, seeded):
    project = _project(worker_url, seeded, "korum")
    run_id = uuid.uuid4()
    end = date(2026, 8, 31)
    with project_scope(project.id, caller="collector:gsc_performance", url=worker_url) as s:
        # partial rows from before the outage, then the outage itself
        for d in range(1, 11):
            s.insert("raw_gsc_performance", {"run_id": run_id, "date": date(2026, 8, d), "query": "q", "page": "https://korum.worldhire.com/", "clicks": 10, "impressions": 100, "position": 4.2})
        s.insert("collection_gaps", {"run_id": run_id, "collector": "gsc_performance", "reason": "searchAnalytics.query returned 503", "affected_scope": "2026-08-11..2026-08-31"})
    rt = Runtime(db_url=worker_url, llm=FakeLLM({"report": commentary_from_metrics}), model="fake")
    artifact = rt.run_analyst(project, run_id, "report", {"since": "2026-08-01", "until": end.isoformat()})
    by = {m.name: m for m in artifact.metrics}
    assert by["gsc_clicks"].value is None and by["gsc_impressions"].value is None, "no partial sum passed off as the month"
    assert any(c.collector == "gsc_performance" and "503" in c.reason for c in artifact.caveats)
    assert artifact.cutoff_date == "2026-08-31"
    assert "unavailable" in artifact.commentary
    assert not re.search(r"\b100\b|\b1000\b", artifact.commentary), "nothing interpolated from the partial rows"
    gate = rt.run_gate_stage1(project, run_id, "report", artifact)
    assert not gate.blocked


def test_report_with_full_data_sums_in_code(worker_url, seeded):
    project = _project(worker_url, seeded, "worldhire")
    run_id = uuid.uuid4()
    with project_scope(project.id, caller="collector:gsc_performance", url=worker_url) as s:
        for d in range(1, 4):
            s.insert("raw_gsc_performance", {"run_id": run_id, "date": date(2026, 7, d), "query": "q", "clicks": 5, "impressions": 50, "position": 3})
            s.insert("raw_gsc_performance", {"run_id": run_id, "date": date(2026, 6, 27 + d), "query": "q", "clicks": 2, "impressions": 20, "position": 5})
    rows = {"raw_gsc_performance": [], "collection_gaps": []}
    with project_scope(project.id, url=worker_url) as s:
        rows["raw_gsc_performance"] = s.fetchall("select * from raw_gsc_performance where project_id = %(project_id)s and run_id = %(run_id)s", {"run_id": run_id})
    inp = AnalystInput(project=project, run_id=run_id, rows=rows, params={"since": "2026-07-01", "until": "2026-07-03"})
    metrics, caveats, period, cutoff = compute_metrics(inp)
    by = {m.name: m for m in metrics}
    assert by["gsc_clicks"].value == 15 and by["gsc_clicks"].prior_value == 6 and by["gsc_clicks"].derived_from_rows == 3
    assert caveats == []


def test_commentary_with_foreign_number_is_rejected(worker_url, seeded):
    project = _project(worker_url, seeded, "worldhire")
    inp = AnalystInput(project=project, run_id=uuid.uuid4(), rows={"raw_gsc_performance": [], "collection_gaps": []}, params={"since": "2026-07-01", "until": "2026-07-03"})
    ctx = AnalystContext(llm=FakeLLM({"report": lambda s, u, sc: {"commentary": "Traffic grew 37% and clicks reached 4200."}}), model="fake")
    with pytest.raises(ValueError, match="numbers not present"):
        run_report(ctx, inp)
    assert foreign_numbers("cutoff 2026-07-03, clicks 15", {"2026-07-03", "15", "2026", "07", "03", "7", "3"}) == []


def test_trend_detects_only_sourced_events(worker_url, seeded):
    project = _project(worker_url, seeded, "korum")
    now = datetime.now(UTC)
    rows = {
        "raw_search_status": [{"id": uuid.uuid4(), "update_name": "August 2026 core update", "status": "Resolved", "started_at": now, "ended_at": None, "source_url": "https://status.search.google.com/incidents/abc", "collected_at": now}],
        "raw_serp": [
            {"id": uuid.uuid4(), "query": "hiring platform", "collected_at": now - timedelta(days=7), "ai_overview_present": False, "results": [{"rank": 12, "domain": "korum.worldhire.com"}], "ai_overview_citations": []},
            {"id": uuid.uuid4(), "query": "hiring platform", "collected_at": now, "ai_overview_present": True, "results": [{"rank": 8, "domain": "korum.worldhire.com"}], "ai_overview_citations": []},
            {"id": uuid.uuid4(), "query": "single sample", "collected_at": now, "ai_overview_present": True, "results": [], "ai_overview_citations": []},
        ],
        "mentions": [],
    }
    events = detect_trends(AnalystInput(project=project, run_id=uuid.uuid4(), rows=rows))
    kinds = sorted(e.kind for e in events)
    assert kinds == ["ai_overview_change", "algorithm_update", "ranking_shift"]
    assert all(e.evidence_ref and e.source_url for e in events)


def test_weekly_monitor_end_to_end_with_serp_gap(worker_url, seeded):
    project = _project(worker_url, seeded, "korum")
    rt = Runtime(db_url=worker_url, model="fake", collector_overrides={"*": {"_transport": FakeSite().transport}},
                 llm=FakeLLM({"trend": lambda s, u, sc: {"details": []}, "report": commentary_from_metrics}))
    h = run_workflow(rt, project, "weekly_monitor", "test", f"week:test-{uuid.uuid4().hex[:6]}")
    assert h.status == "done"
    with project_scope(project.id, url=worker_url) as s:
        rep = s.fetchone("select caveats, commentary from reports where project_id = %(project_id)s and run_id = %(id)s", {"id": h.id})
        gaps = s.fetchall("select collector from collection_gaps where project_id = %(project_id)s and run_id = %(id)s", {"id": h.id})
    assert rep is not None
    assert {g["collector"] for g in gaps} >= {"serp"}, "no DataForSEO credentials in tests: that is a gap"
    assert any(c["collector"] == "serp" for c in rep["caveats"])


def test_digest_batches_and_critical_breaks_through(worker_url, seeded):
    project = _project(worker_url, seeded, "rejuveluxe")
    run_id = uuid.uuid4()
    sent = []
    notify.add_transport(lambda subject, body, meta: sent.append((subject, meta)))
    try:
        with project_scope(project.id, url=worker_url) as s:
            for i in range(3):
                approvals.queue_approval(s, run_id, "open_fix_pr", {"i": i}, f"fix {i}", "analyst:technical", {"kind": "close_pr_and_delete_branch"})
            with pytest.raises(approvals.ReversalMissing):
                approvals.queue_approval(s, run_id, "open_fix_pr", {}, "x", "analyst:technical", {})
            d = notify.daily_digest(s)
            assert d["count"] == 3
            assert notify.daily_digest(s) is None, "already notified"
            notify.page_owner(s, run_id, [{"rule_key": "no_indexable_checkout", "assertion": "must_noindex", "detail": "x", "url": "u"}])
    finally:
        notify._transports.clear()
    assert any("digest" in sub for sub, _ in sent) and any(meta.get("severity") == "critical" for _, meta in sent)


def test_only_project_approver_can_decide(worker_url, seeded):
    project = _project(worker_url, seeded, "rejuveluxe")
    with project_scope(project.id, url=worker_url) as s:
        aid = approvals.queue_approval(s, uuid.uuid4(), "open_fix_pr", {}, "fix", "analyst:technical", {"kind": "close_pr_and_delete_branch"})
        with pytest.raises(PermissionError):
            approvals.decide(s, aid, uuid.uuid4(), True)
        row = approvals.decide(s, aid, project.approver_id, True)
        assert row["status"] == "approved"
        with pytest.raises(ValueError):
            approvals.decide(s, aid, project.approver_id, True)


def test_schedule_staggers_projects():
    projects = [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}]
    monday_0600 = datetime(2026, 9, 14, 6, 0, tzinfo=IST)   # a Monday
    assert [(p["slug"], w) for p, w, _ in due(monday_0600.astimezone(UTC), projects)] == [("a", "weekly_monitor")]
    assert [(p["slug"], w) for p, w, _ in due((monday_0600 + timedelta(minutes=20)).astimezone(UTC), projects)] == [("b", "weekly_monitor")]
    fourth = datetime(2026, 10, 4, 6, 0, tzinfo=IST)
    hits = due(fourth.astimezone(UTC), projects)
    assert [(p["slug"], w, ld) for p, w, ld in hits] == [("a", "monthly_full", "month:2026-09")]
