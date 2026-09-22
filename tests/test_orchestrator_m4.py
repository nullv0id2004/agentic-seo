"""M4 acceptance: a deploy webhook produces issues with resolving evidence refs, a duplicate webhook
produces no second run, and a forced budget breach halts before dispatch rather than after."""
import uuid

import pytest

from contracts.project import Project
from db.connection import project_scope
from llm.fake import FakeLLM
from orchestrator.runner import run_workflow
from orchestrator.runtime import Runtime
from orchestrator.webhooks import handle_generic_deploy, handle_vercel_deploy, verify_bearer
from tests.conftest import requires_db
from tests.fakesite import FakeSite

pytestmark = [requires_db, pytest.mark.db]


def explain(system, user, schema):
    import json
    issues = json.loads(user.split("Issues:\n", 1)[1])
    return {"explanations": [{"index": i["index"], "recommended_fix": f"Fix {i['issue_type']}.", "claude_code_prompt": f"Fix {i['issue_type']} at {i['url']}"} for i in issues]}


def _rt(worker_url, site: FakeSite | None = None) -> Runtime:
    site = site or FakeSite()
    return Runtime(db_url=worker_url, llm=FakeLLM({"technical": explain}), model="fake-model",
                   collector_overrides={"*": {"_transport": site.transport}})


def _project(worker_url, seeded, slug="korum") -> Project:
    with project_scope(seeded[slug], url=worker_url) as s:
        return Project.from_row(s.fetchone("select * from projects where id = %(project_id)s"))


def _vercel(deployment_id: str, name="korum"):
    return {"type": "deployment.succeeded", "payload": {"target": "production", "project": {"name": name},
            "deployment": {"id": deployment_id, "url": "korum.worldhire.com"}, "alias": ["korum.worldhire.com"]}}


def test_deploy_webhook_produces_issues_with_resolving_evidence(worker_url, seeded):
    rt = _rt(worker_url)
    dep = f"dpl_{uuid.uuid4().hex[:8]}"
    out = handle_vercel_deploy(rt, _vercel(dep))
    assert out["project"] == "korum" and out["created"] is True
    run_id = uuid.UUID(out["run_id"])
    with project_scope(seeded["korum"], url=worker_url) as s:
        run = s.fetchone("select status, cost_usd, tokens_in from runs where project_id = %(project_id)s and id = %(id)s", {"id": run_id})
        issues = s.fetchall("select issue_type, severity, url, evidence_ref, claude_code_prompt from issues where project_id = %(project_id)s and run_id = %(id)s", {"id": run_id})
        gate = s.fetchone("select stage1_violations, stage2_verdict from gate_results where project_id = %(project_id)s and run_id = %(id)s", {"id": run_id})
        resolving = s.fetchall(
            "select i.id from issues i join raw_crawl_pages r on r.id = i.evidence_ref and r.project_id = i.project_id "
            "where i.project_id = %(project_id)s and i.run_id = %(id)s", {"id": run_id})
    assert run["status"] == "done", run
    assert float(run["cost_usd"]) > 0 and run["tokens_in"] > 0, "cost accounting from agent_logs"
    assert issues, "the fake site has thin pages, missing meta descriptions and so on"
    assert len(resolving) == len(issues), "every issue's evidence_ref resolves to a raw row"
    assert gate["stage2_verdict"] is None and not any(v["severity"] == "block" for v in gate["stage1_violations"])
    assert all(i["evidence_ref"] is not None for i in issues)
    assert not any(i["url"] and "/recruiter/" in i["url"] for i in issues), "protected paths never appear in artifacts"


def test_duplicate_webhook_produces_no_second_run(worker_url, seeded):
    rt = _rt(worker_url)
    dep = f"dpl_{uuid.uuid4().hex[:8]}"
    first = handle_vercel_deploy(rt, _vercel(dep))
    second = handle_vercel_deploy(rt, _vercel(dep))
    assert first["created"] is True and second["created"] is False
    assert first["run_id"] == second["run_id"]
    with project_scope(seeded["korum"], url=worker_url) as s:
        n = s.fetchone("select count(*) as n from runs where project_id = %(project_id)s and workflow = 'post_deploy_audit' and idempotency_key = (select idempotency_key from runs where project_id = %(project_id)s and id = %(id)s)", {"id": uuid.UUID(first["run_id"])})["n"]
        audit = s.fetchall("select event from audit_log where project_id = %(project_id)s and run_id = %(id)s", {"id": uuid.UUID(first["run_id"])})
    assert n == 1
    assert "duplicate_trigger_skipped" in {a["event"] for a in audit}


def test_forced_budget_breach_halts_before_dispatch(worker_url, seeded):
    rt = _rt(worker_url)
    with project_scope(seeded["worldhire"], url=worker_url) as s:
        s.execute("update projects set monthly_cost_cap_usd = 0.01 where id = %(project_id)s")
    project = _project(worker_url, seeded, "worldhire")
    handle = run_workflow(rt, project, "post_deploy_audit", "test", f"deploy:{uuid.uuid4().hex[:8]}")
    assert handle.status == "halted_budget"
    with project_scope(seeded["worldhire"], url=worker_url) as s:
        run = s.fetchone("select status, halt_reason from runs where project_id = %(project_id)s and id = %(id)s", {"id": handle.id})
        collected = s.fetchone("select count(*) as n from raw_crawl_pages where project_id = %(project_id)s and run_id = %(id)s", {"id": handle.id})["n"]
        logs = s.fetchone("select count(*) as n from agent_logs where project_id = %(project_id)s and run_id = %(id)s", {"id": handle.id})["n"]
        s.execute("update projects set monthly_cost_cap_usd = 50 where id = %(project_id)s")
    assert run["status"] == "halted_budget" and "exceeds remaining" in run["halt_reason"]
    assert collected == 0 and logs == 0, "halted before any collector or analyst ran"
    assert not rt.llm.calls


def test_budget_projection_uses_history(worker_url, seeded):
    from orchestrator import budget
    with project_scope(seeded["rejuveluxe"], url=worker_url) as s:
        for cost in (1.0, 2.0, 3.0):
            s.insert("runs", {"workflow": "monthly_full", "trigger": "t", "idempotency_key": uuid.uuid4().hex, "status": "done", "cost_usd": cost})
        assert budget.project_cost(s, "monthly_full") == 2.0
        assert budget.project_cost(s, "content_pipeline") == budget.DEFAULT_ESTIMATE_USD["content_pipeline"]
        d = budget.decide(s, "monthly_full", monthly_cap_usd=7.5)
        assert not d.allowed and d.remaining_usd == 1.5
        assert budget.decide(s, "monthly_full", monthly_cap_usd=50).allowed


def test_leaked_route_pages_owner_and_queues_critical_approval(worker_url, seeded):
    rt = _rt(worker_url, FakeSite(leaked_inbox=True))
    project = _project(worker_url, seeded, "korum")
    handle = run_workflow(rt, project, "post_deploy_audit", "test", f"deploy:{uuid.uuid4().hex[:8]}")
    with project_scope(seeded["korum"], url=worker_url) as s:
        crit = s.fetchall("select action_type, severity, reversal_payload from approvals where project_id = %(project_id)s and run_id = %(id)s and severity = 'critical'", {"id": handle.id})
        notified = s.fetchall("select detail from audit_log where project_id = %(project_id)s and run_id = %(id)s and event = 'notified'", {"id": handle.id})
        issues = s.fetchall("select issue_type, severity, url from issues where project_id = %(project_id)s and run_id = %(id)s and issue_type = 'critical_rule_violation'", {"id": handle.id})
    assert any(a["action_type"] == "page_owner" for a in crit)
    assert any("CRITICAL" in n["detail"]["subject"] for n in notified)
    assert issues and all(i["url"] is None for i in issues), "leaked path is cited by raw row id, never spelled out"


def test_resume_after_crash_does_not_duplicate_collection(worker_url, seeded):
    """P6: a crash mid-graph followed by a retry resumes from the checkpoint; the earlier nodes do not rerun."""
    from orchestrator.graphs import post_deploy_audit
    from orchestrator.runs import open_run

    rt = _rt(worker_url)
    project = _project(worker_url, seeded, "korum")
    with rt.scope(project.id) as s:
        handle = open_run(s, "post_deploy_audit", "test", f"deploy:{uuid.uuid4().hex[:8]}")
    calls = {"n": 0}

    def flaky(system, user, schema):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("model unavailable")
        return explain(system, user, schema)

    rt.llm = FakeLLM({"technical": flaky})
    from orchestrator.checkpointer import checkpointer

    initial = {"project_id": str(project.id), "run_id": str(handle.id), "params": {}, "collectors": {}, "artifacts": {}, "gate": {}, "issues": [], "approvals": [], "errors": [], "status": "running"}
    config = {"configurable": {"thread_id": str(handle.id)}}
    with checkpointer(worker_url) as cp:
        graph = post_deploy_audit.build(rt).compile(checkpointer=cp)
        with pytest.raises(RuntimeError):
            graph.invoke(initial, config)
        final = graph.invoke(None, config)  # resume from the checkpoint
    assert final["status"] == "done"
    with project_scope(project.id, url=worker_url) as s:
        crawls = s.fetchone("select count(*) as n from agent_logs where project_id = %(project_id)s and run_id = %(id)s and agent = 'site_crawl'", {"id": handle.id})["n"]
    assert crawls == 1, "the crawl ran once; the resume started at the failed node"


def test_generic_deploy_webhook_is_idempotent_and_production_only(worker_url, seeded):
    rt = _rt(worker_url)
    dep = f"sha-{uuid.uuid4().hex[:8]}"
    first = handle_generic_deploy(rt, {"project": "korum", "deployment_id": dep, "changed_urls": ["/about"]})
    second = handle_generic_deploy(rt, {"project": "korum", "deployment_id": dep})
    assert first["created"] is True and second["created"] is False and first["run_id"] == second["run_id"]
    assert "ignored" in handle_generic_deploy(rt, {"project": "korum", "deployment_id": "x", "environment": "preview"})
    assert "ignored" in handle_generic_deploy(rt, {"project": "cruise-guru", "deployment_id": "x"})
    assert verify_bearer("Bearer s3cret", "s3cret") and not verify_bearer("Bearer nope", "s3cret") and not verify_bearer("Bearer s3cret", None)


def test_stage1_violations_are_stored_as_jsonb(worker_url, seeded):
    """Regression: the first production audit failed with "cannot adapt type 'dict'" because a
    non-empty violation list was sent as a Postgres array instead of jsonb."""
    from contracts.artifacts import IssueOut, TechnicalReport

    rt = _rt(worker_url)
    project = _project(worker_url, seeded)
    with project_scope(seeded["korum"], url=worker_url) as s:
        run_id = s.insert("runs", {"workflow": "post_deploy_audit", "trigger": "test", "status": "running",
                                   "idempotency_key": uuid.uuid4().hex})
    with project_scope(seeded["korum"], caller="collector:site_crawl", url=worker_url) as s:
        page = {"id": s.insert("raw_crawl_pages", {"run_id": run_id, "url": "https://korum.worldhire.com/", "status_code": 200,
                                                    "title": "KORUM", "meta_description": None})}
    artifact = TechnicalReport(agent="technical", issues=[IssueOut(
        evidence_ref=page["id"], issue_type="thin_content", severity="medium", url="https://korum.worldhire.com/",
        evidence="the page has under 100 words", recommended_fix="Add a paragraph — then republish.", claude_code_prompt="x")])
    result = rt.run_gate_stage1(project, run_id, "technical", artifact)
    assert result.blocked
    with project_scope(seeded["korum"], url=worker_url) as s:
        gate = s.fetchone("select stage1_violations from gate_results where project_id = %(project_id)s and run_id = %(id)s", {"id": run_id})
    assert isinstance(gate["stage1_violations"], list) and gate["stage1_violations"][0]["severity"] == "block"
