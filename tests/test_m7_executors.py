"""M7 acceptance: every approval type in the queue can be reversed from its stored payload, one of each.
Also: github_executor never touches main and never merges; nothing executes without an approved row."""
import json
import uuid

import httpx
import pytest

from contracts.project import Project
from db.connection import project_scope
from executors.base import NotApproved
from executors.cms import CMSExecutor
from executors.dispatch import execute_approved, execute_one, reverse_one
from executors.email import EmailExecutor
from executors.github import GitHubExecutor
from executors.internal import AcknowledgeExecutor, KeywordMappingExecutor
from orchestrator import approvals
from orchestrator.runtime import Runtime
from tests.conftest import requires_db
from tests.fakegithub import FakeGitHub

pytestmark = [requires_db, pytest.mark.db]


class FakeSMTP:
    sent: list[dict] = []

    def __init__(self, host, port):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append({"to": msg["To"], "subject": msg["Subject"], "body": msg.get_content()})


class FakeCMS:
    def __init__(self):
        self.published: dict[str, dict] = {}
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            body = json.loads(req.content)
            slug = body["external_id"][:8]
            url = f"https://rejuveluxe.com/journal/{slug}"
            self.published[url] = body
            return httpx.Response(201, json={"url": url, "unpublish_url": url})
        if req.method == "DELETE":
            self.published.pop(str(req.url), None)
            return httpx.Response(204)
        return httpx.Response(404)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("SEO_SECRET_BACKEND", "env")
    monkeypatch.setenv("SEO_SECRET_GITHUB_FIX_BRANCH_TOKEN", "ghp_test")
    monkeypatch.setenv("SEO_SECRET_CMS_CONTENT_WRITE_TOKEN", "cms_test")
    monkeypatch.setenv("SEO_SECRET_OUTREACH_SMTP", json.dumps({"host": "smtp.test", "port": 587, "user": "u", "password": "p", "from": "seo@worldhire.com"}))
    from config.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _project(worker_url, seeded, slug) -> Project:
    with project_scope(seeded[slug], url=worker_url) as s:
        return Project.from_row(s.fetchone("select * from projects where id = %(project_id)s"))


def _approve(worker_url, project: Project, action: str, payload: dict, reversal: dict, run_id=None) -> uuid.UUID:
    run_id = run_id or uuid.uuid4()
    with project_scope(project.id, url=worker_url) as s:
        aid = approvals.queue_approval(s, run_id, action, payload, f"test {action}", "test", reversal)
        approvals.decide(s, aid, project.approver_id, True)
    return aid


def _status(worker_url, project, aid):
    with project_scope(project.id, url=worker_url) as s:
        return s.fetchone("select status, reversal_payload, execution_result from approvals where project_id = %(project_id)s and id = %(id)s", {"id": aid})


def test_every_approval_type_executes_and_reverses_from_stored_payload(worker_url, seeded, env):
    gh = FakeGitHub()
    cms = FakeCMS()
    FakeSMTP.sent.clear()
    executors = {}
    for ex in (GitHubExecutor(transport=gh.transport), CMSExecutor(transport=cms.transport), EmailExecutor(smtp_factory=FakeSMTP), KeywordMappingExecutor(), AcknowledgeExecutor()):
        for a in ex.action_types:
            executors[a] = ex
    rt = Runtime(db_url=worker_url, model="fake")
    korum = _project(worker_url, seeded, "korum")
    rl = _project(worker_url, seeded, "rejuveluxe")
    with project_scope(rl.id, url=worker_url) as s:
        s.execute("update projects set cms_publish_url = 'https://cms.rejuveluxe.test/publish' where id = %(project_id)s")
        brief_id = s.insert("content_briefs", {"title": "Retinol basics", "answer_block": "a", "outline": [], "draft": "d", "sources": [], "status": "draft"})
        pitch_id = s.insert("pitches", {"run_id": uuid.uuid4(), "outlet_url": "https://outlet.example/", "subject": "Data", "body": "Hello"})
        s.execute("insert into keywords (project_id, keyword, mapped_url) values (%(project_id)s, 'retinol serum', 'https://rejuveluxe.com/old') on conflict do nothing")

    cases = {
        "open_fix_pr": (korum, {"issue_type": "missing_meta_description", "url": "https://korum.worldhire.com/blog/one", "recommended_fix": "Add one.", "claude_code_prompt": "Add metadata."}, {"kind": "close_pr_and_delete_branch"}),
        "robots_change": (korum, {"file_changes": [{"path": "public/robots.txt", "content": "User-agent: *\nDisallow: /recruiter/\n"}], "title": "robots: disallow recruiter"}, {"kind": "restore_previous_robots"}),
        "sitemap_change": (korum, {"file_changes": [{"path": "public/sitemap.xml", "content": "<urlset/>"}]}, {"kind": "restore_previous_sitemap"}),
        "canonical_change": (korum, {"file_changes": [{"path": "app/layout.tsx", "content": "// canonical"}]}, {"kind": "restore_previous_canonical"}),
        "hreflang_change": (korum, {"file_changes": [{"path": "app/head.tsx", "content": "// hreflang"}]}, {"kind": "restore_previous_hreflang"}),
        "publish_content": (rl, {"brief_id": str(brief_id), "title": "Retinol basics"}, {"kind": "unpublish", "brief_id": str(brief_id)}),
        "send_pitch": (rl, {"pitch_id": str(pitch_id), "to": "editor@outlet.example", "subject": "Data", "body": "Hello"}, {"kind": "send_retraction"}),
        "keyword_mapping": (rl, {"mappings": [{"keyword": "retinol serum", "mapped_url": "https://rejuveluxe.com/products/serum"}]}, {"kind": "restore_previous_mapping", "previous": []}),
        "page_owner": (korum, {"violations": []}, {"kind": "acknowledge"}),
        "spend_above_cap": (korum, {"usd": 12}, {"kind": "no_op_reject"}),
    }
    assert set(cases) == set(approvals.APPROVAL_ACTIONS), "one of each approval type"
    ids = {}
    for action, (project, payload, reversal) in cases.items():
        aid = _approve(worker_url, project, action, payload, reversal)
        res = execute_one(rt, project.id, aid, executors)
        assert res["ok"], action
        row = _status(worker_url, project, aid)
        assert row["status"] == "executed" and row["reversal_payload"]["kind"], action
        ids[action] = (project, aid)

    # effects happened
    assert len(gh.pulls) == 5 and all(p["base"] == "main" and p["head"].startswith("seo-fix/") for p in gh.pulls.values())
    assert gh.files[("main", "public/robots.txt")] == "User-agent: *\nAllow: /\n", "main untouched"
    assert gh.pushes_to_default == 0 and gh.merge_attempts == 0
    assert len(cms.published) == 1 and FakeSMTP.sent[-1]["to"] == "editor@outlet.example"
    with project_scope(rl.id, url=worker_url) as s:
        assert s.fetchone("select mapped_url from keywords where project_id = %(project_id)s and keyword = 'retinol serum'")["mapped_url"] == "https://rejuveluxe.com/products/serum"
        assert s.fetchone("select status, published_url from content_briefs where project_id = %(project_id)s and id = %(id)s", {"id": brief_id})["status"] == "published"

    # every one of them reverses from what is stored on the row, nothing else
    for action, (project, aid) in ids.items():
        res = reverse_one(rt, project.id, aid, executors)
        assert res["ok"], action
        assert _status(worker_url, project, aid)["status"] == "reversed", action
    assert all(p["state"] == "closed" for p in gh.pulls.values())
    assert not any(b.startswith("seo-fix/") for b in gh.branches), "fix branches deleted"
    robots_reversal = _status(worker_url, korum, ids["robots_change"][1])["reversal_payload"]
    assert robots_reversal["prior_files"][0]["content"] == "User-agent: *\nAllow: /\n", "the prior robots.txt is on the row"
    assert cms.published == {}
    assert "disregard" in FakeSMTP.sent[-1]["body"]
    with project_scope(rl.id, url=worker_url) as s:
        assert s.fetchone("select mapped_url from keywords where project_id = %(project_id)s and keyword = 'retinol serum'")["mapped_url"] == "https://rejuveluxe.com/old"
        assert s.fetchone("select status from content_briefs where project_id = %(project_id)s and id = %(id)s", {"id": brief_id})["status"] == "unpublished"
        assert s.fetchone("select status from pitches where project_id = %(project_id)s and id = %(id)s", {"id": pitch_id})["status"] == "retracted"


def test_nothing_executes_without_an_approved_row(worker_url, seeded, env):
    gh = FakeGitHub()
    rt = Runtime(db_url=worker_url, model="fake")
    korum = _project(worker_url, seeded, "korum")
    with project_scope(korum.id, url=worker_url) as s:
        pending = approvals.queue_approval(s, uuid.uuid4(), "open_fix_pr", {"issue_type": "x", "recommended_fix": "y"}, "t", "test", {"kind": "close_pr_and_delete_branch"})
        rejected = approvals.queue_approval(s, uuid.uuid4(), "open_fix_pr", {"issue_type": "x", "recommended_fix": "y"}, "t", "test", {"kind": "close_pr_and_delete_branch"})
        approvals.decide(s, rejected, korum.approver_id, False)
    ex = {"open_fix_pr": GitHubExecutor(transport=gh.transport)}
    for aid in (pending, rejected):
        with pytest.raises(NotApproved):
            execute_one(rt, korum.id, aid, ex)
    assert gh.pulls == {}
    assert execute_approved(rt, korum.id, ex) == [], "the dispatcher only sees approved rows"
    with pytest.raises(NotApproved):
        reverse_one(rt, korum.id, pending, ex)


def test_github_executor_refuses_merge_endpoint(env):
    gh = FakeGitHub()
    ex = GitHubExecutor(transport=gh.transport)
    with ex._client() as http, pytest.raises(PermissionError):
        ex._call(http, "PUT", f"/repos/{gh.repo}/pulls/1/merge")
    assert gh.merge_attempts == 0


def test_execution_failure_is_recorded_and_row_stays_approved(worker_url, seeded, env):
    rt = Runtime(db_url=worker_url, model="fake")
    korum = _project(worker_url, seeded, "korum")
    boom = httpx.MockTransport(lambda req: httpx.Response(500, json={"message": "down"}))
    aid = _approve(worker_url, korum, "open_fix_pr", {"issue_type": "x", "recommended_fix": "y"}, {"kind": "close_pr_and_delete_branch"})
    out = execute_approved(rt, korum.id, {"open_fix_pr": GitHubExecutor(transport=boom)})
    assert out and not out[0]["ok"]
    row = _status(worker_url, korum, aid)
    assert row["status"] == "approved" and row["execution_result"]["ok"] is False
