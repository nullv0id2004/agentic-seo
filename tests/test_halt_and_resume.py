"""Section 14: an indexable protected route halts every workflow for the project until a probe is clean.
P6: a retry after a failed run resumes from the checkpoint rather than skipping or duplicating."""
import uuid

import pytest

from contracts.project import Project
from db.connection import project_scope
from llm.fake import FakeLLM
from orchestrator.runner import run_workflow
from orchestrator.runtime import Runtime
from tests.conftest import requires_db
from tests.fakesite import FakeSite
from tests.test_orchestrator_m4 import explain

pytestmark = [requires_db, pytest.mark.db]


def _project(worker_url, seeded, slug) -> Project:
    with project_scope(seeded[slug], url=worker_url) as s:
        return Project.from_row(s.fetchone("select * from projects where id = %(project_id)s"))


def _rt(worker_url, site, llm=None):
    return Runtime(db_url=worker_url, model="fake", llm=llm or FakeLLM({"technical": explain}), collector_overrides={"*": {"_transport": site.transport}})


def test_indexed_protected_route_halts_project_until_probe_is_clean(worker_url, seeded):
    project = _project(worker_url, seeded, "worldhire")
    leaked = _rt(worker_url, FakeSite(leaked_inbox=True))
    h = run_workflow(leaked, project, "post_deploy_audit", "test", f"deploy:{uuid.uuid4().hex[:6]}")
    assert h.status == "paused_for_approval"
    with project_scope(project.id, url=worker_url) as s:
        assert s.fetchone("select halted_reason from projects where id = %(project_id)s")["halted_reason"]
    # every other workflow is skipped while halted
    week = f"week:halt-{uuid.uuid4().hex[:6]}"
    skipped = run_workflow(leaked, project, "weekly_monitor", "cron", week)
    assert skipped.status == "skipped"
    with project_scope(project.id, url=worker_url) as s:
        row = s.fetchone("select status, halt_reason from runs where project_id = %(project_id)s and id = %(id)s", {"id": skipped.id})
        assert row["status"] == "skipped" and "project halted" in row["halt_reason"]
        assert s.fetchone("select count(*) as n from raw_serp where project_id = %(project_id)s and run_id = %(id)s", {"id": skipped.id})["n"] == 0
    # an acknowledgement alone does not lift it; a clean probe does
    clean = _rt(worker_url, FakeSite(leaked_inbox=False), llm=FakeLLM({"technical": explain, "trend": lambda s, u, sc: {"details": []},
                                                                     "report": lambda s, u, sc: {"commentary": "Cutoff stated. Nothing else."}}))
    probe = run_workflow(clean, project, "daily_probe", "cron", f"probe:{uuid.uuid4().hex[:6]}")
    assert probe.status == "done"
    with project_scope(project.id, url=worker_url) as s:
        assert s.fetchone("select halted_reason from projects where id = %(project_id)s")["halted_reason"] is None
        assert s.fetchone("select count(*) as n from audit_log where project_id = %(project_id)s and event = 'project_halt_cleared'")["n"] >= 1
    # the skipped logical week can now run
    rerun = run_workflow(clean, project, "weekly_monitor", "cron", week)
    assert rerun.id == skipped.id and rerun.status == "done"


def test_failed_run_resumes_from_checkpoint_on_retry(worker_url, seeded):
    project = _project(worker_url, seeded, "korum")
    calls = {"n": 0}

    def flaky(system, user, schema):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("model unavailable")
        return explain(system, user, schema)

    rt = _rt(worker_url, FakeSite(), llm=FakeLLM({"technical": flaky}))
    logical = f"deploy:{uuid.uuid4().hex[:6]}"
    with pytest.raises(RuntimeError):
        run_workflow(rt, project, "post_deploy_audit", "webhook", logical)
    with project_scope(project.id, url=worker_url) as s:
        failed = s.fetchone("select id, status from runs where project_id = %(project_id)s and workflow = 'post_deploy_audit' order by started_at desc limit 1")
    assert failed["status"] == "failed"
    h = run_workflow(rt, project, "post_deploy_audit", "webhook", logical)
    assert h.id == failed["id"] and h.status == "done" and h.created is False
    with project_scope(project.id, url=worker_url) as s:
        crawls = s.fetchone("select count(*) as n from agent_logs where project_id = %(project_id)s and run_id = %(id)s and agent = 'site_crawl'", {"id": h.id})["n"]
        events = {r["event"] for r in s.fetchall("select event from audit_log where project_id = %(project_id)s and run_id = %(id)s", {"id": h.id})}
    assert crawls == 1, "the crawl did not run again; the graph resumed at the analyst"
    assert "run_resumed" in events
