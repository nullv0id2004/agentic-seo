"""Two failures in worldhire's first full audit (25 Oct): one brand-rule word blocked every technical issue, and one
Search Console query with a '?' cost every keyword its search volume."""
import json
import uuid

import httpx
import pytest

from analysts import technical
from analysts.base import AnalystContext, AnalystInput
from collectors.keyword_metrics import usable
from collectors.registry import collect
from contracts.project import Project
from db.connection import project_scope
from gate.stage1_rules import ProjectRules, run_stage1
from llm.fake import FakeLLM
from tests.conftest import requires_db

BRAND = [{"rule_type": "banned_phrase", "pattern": r"\bCandidate\b", "severity": "block", "active": True},
         {"rule_type": "char_ban", "pattern": "—", "severity": "block", "active": True}]


def _project() -> Project:
    return Project(id=uuid.uuid4(), slug="worldhire", display_name="WorldHire", domains=["worldhire.com"], vertical="recruitment",
                   approver_id=uuid.uuid4(), monthly_cost_cap_usd=50, enabled_agents=["technical"])


def test_a_brand_word_in_one_fix_no_longer_blocks_every_technical_issue():
    pages = [{"id": uuid.uuid4(), "url": f"https://worldhire.com/legal/{slug}", "status_code": 200, "word_count": 400, "title": f"{slug} | WorldHire",
              "h1": [], "meta_description": "d", "canonical": f"https://worldhire.com/legal/{slug}"} for slug in ("candidate-consent-policy", "terms-of-use")]
    rows = {"raw_crawl_pages": pages, "raw_vitals": [], "raw_sitemap_urls": [], "critical_rules": [], "raw_url_inspection": [], "brand_rules": BRAND}

    def explain(system, user, schema):
        issues = json.loads(user.split("Issues:\n", 1)[1])
        return {"explanations": [{"index": i["index"],
                                  "recommended_fix": "Add an h1 naming the Candidate consent policy." if "candidate" in (i["url"] or "") else "Add one h1.",
                                  "claude_code_prompt": "Add the h1."} for i in issues]}

    report = technical.run(AnalystContext(llm=FakeLLM({"technical": explain}), model="fake"),
                           AnalystInput(project=_project(), run_id=uuid.uuid4(), rows=rows))
    by_url = {i.url: i for i in report.issues if i.issue_type == "missing_h1"}
    flagged = by_url["https://worldhire.com/legal/candidate-consent-policy"]
    assert flagged.recommended_fix == "Fix the missing h1 described in the evidence." and flagged.claude_code_prompt is None
    assert by_url["https://worldhire.com/legal/terms-of-use"].recommended_fix == "Add one h1.", "the other issues keep the model's text"
    result = run_stage1("technical", report, ProjectRules(brand_rules=BRAND, critical_rules=[]), lambda refs: set(refs))
    assert not result.blocked, [v.as_dict() for v in result.violations]


def test_keywords_google_ads_refuses_are_left_out():
    assert usable("executive search") and usable("korum")
    assert not usable("korum?") and not usable("jobs (india)") and not usable("x" * 81) and not usable(" ".join(["w"] * 11))


@requires_db
@pytest.mark.db
def test_one_bad_keyword_no_longer_costs_the_batch(worker_url, seeded):
    with project_scope(seeded["worldhire"], url=worker_url) as s:
        project = Project.from_row(s.fetchone("select * from projects where id = %(project_id)s"))
    sent = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)[0]
        if "search_volume" in request.url.path:
            sent.append(body["keywords"])
            if any("?" in k for k in body["keywords"]):   # what Google Ads does to the whole batch
                return httpx.Response(200, json={"tasks": [{"status_code": 40501, "status_message": "Invalid Field: 'keywords'."}]})
            return httpx.Response(200, json={"tasks": [{"status_code": 20000, "result": [{"keyword": k, "search_volume": 90} for k in body["keywords"]]}]})
        return httpx.Response(200, json={"tasks": [{"status_code": 20000, "result": [{"items": []}]}]})

    run_id = uuid.uuid4()
    collect("keyword_metrics", project, run_id, {"_transport": httpx.MockTransport(handle), "keywords": ["executive search", "korum?", "global hiring"]},
            db_url=worker_url)
    with project_scope(project.id, url=worker_url) as s:
        metrics = {r["keyword"] for r in s.fetchall("select keyword from raw_keyword_metrics where project_id = %(project_id)s and run_id = %(r)s", {"r": run_id})}
        gaps = [(g["reason"], g["affected_scope"]) for g in s.fetchall("select reason, affected_scope from collection_gaps where project_id = %(project_id)s and run_id = %(r)s", {"r": run_id})]
    assert not any("korum?" in batch for batch in sent)
    assert {"executive search", "global hiring"} <= metrics
    assert ("keyword has symbols or a length Google Ads refuses", "korum?") in gaps
