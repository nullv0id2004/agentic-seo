"""The link-building analyst: phase, checklist, prospects, anchors and velocity are computed in code from raw rows;
the model only labels prospects and drafts profile descriptions, and its text is checked before it is kept."""
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from analysts.base import AnalystContext, AnalystInput
from analysts.linkbuilding import (
    anchor_bucket,
    anchor_shares,
    checklist,
    choose_tactics,
    clean_copy,
    phase_of,
    phase_signals,
    prospects,
    same_as_hosts,
    velocity,
)
from analysts.linkbuilding import run as run_linkbuilding
from config.loader import load_project_config
from contracts.project import Project
from db.connection import jsonb, project_scope
from llm.fake import FakeLLM
from tests.conftest import requires_db

KORUM = load_project_config(Path(__file__).resolve().parent.parent / "config" / "projects" / "korum.yaml")
RULES = [{"rule_type": r.rule_type, "pattern": r.pattern, "severity": r.severity, "active": True} for r in KORUM.brand_rules]
TODAY = date(2026, 10, 16)
BRAND = {"korum", "worldhire"}


def _id():
    return uuid.uuid4()


def _project() -> Project:
    fields = KORUM.model_dump(include={"slug", "display_name", "domains", "vertical", "enabled_agents", "competitors", "description",
                                       "brand_context_terms", "keyword_seeds"})
    return Project(id=_id(), approver_id=KORUM.approver_id, monthly_cost_cap_usd=50, **fields)


def _home(same_as=()):
    return {"id": _id(), "url": "https://korum.worldhire.com/", "status_code": 200, "word_count": 600, "title": "KORUM: confidential hiring",
            "meta_description": "Senior professionals stay private; verified recruiters connect only with permission.",
            "raw_jsonld": [{"@graph": [{"@type": "Organization", "name": "KORUM", "sameAs": list(same_as)}]}]}


def _rd(role, target, domain, rank=50, spam=2, first_seen=None, lost=None):
    return {"id": _id(), "role": role, "target": target, "domain": domain, "rank": rank, "spam_score": spam, "first_seen": first_seen, "lost_date": lost}


def test_phase_is_the_lower_median_of_what_the_rows_show():
    home = _home()
    young = {"id": _id(), "rank": 4, "referring_domains": 3, "first_seen": datetime(2026, 8, 1, tzinfo=UTC)}
    pages = [home] + [{"id": _id(), "url": f"https://korum.worldhire.com/b{i}", "status_code": 200, "word_count": 500} for i in range(30)]
    sig = phase_signals(young, pages, listed=1, total_targets=18, page_ref=home["id"], today=TODAY)
    assert {s.name: s.points_to for s in sig} == {"domain_rank": "foundation", "site_age_years": "foundation", "referring_domains": None,
                                                 "indexable_pages": "growth", "profiles_listed": "foundation"}
    assert phase_of(sig) == "foundation"
    older = {"id": _id(), "rank": 55, "referring_domains": 900, "first_seen": datetime(2021, 1, 1, tzinfo=UTC)}
    sig = phase_signals(older, pages, listed=2, total_targets=18, page_ref=home["id"], today=TODAY)
    assert phase_of(sig) == "growth", "authority on rank and age, but thin profiles hold it at growth"
    assert phase_of([]) is None, "nothing measured is no phase, not a guess"
    assert all(s.evidence_ref for s in sig)


def test_a_profile_is_done_only_in_same_as_and_a_directory_when_it_links():
    home = _home(["https://www.linkedin.com/company/korum", "https://in.linkedin.com/x"])
    same = same_as_hosts([home])
    assert same == {"linkedin.com": home["id"]}
    targets = [{"platform": "LinkedIn", "domain": "linkedin.com", "kind": "profile", "priority": 10},
               {"platform": "Product Hunt", "domain": "producthunt.com", "kind": "profile", "priority": 20},
               {"platform": "G2", "domain": "g2.com", "kind": "review", "priority": 30},
               {"platform": "Capterra", "domain": "capterra.com", "kind": "review", "priority": 40, "note": "Pick the recruiting category."},
               {"platform": "Old", "domain": "old.example", "kind": "directory", "active": False}]
    ph, g2 = _id(), _id()
    items = {i.platform: i for i in checklist(targets, same, {"producthunt.com": ph, "g2.com": g2}, home["id"], "KORUM", "korum.worldhire.com")}
    assert set(items) == {"LinkedIn", "Product Hunt", "G2", "Capterra"}, "inactive targets are not on the list"
    assert items["LinkedIn"].status == "done" and items["LinkedIn"].in_same_as
    assert items["Product Hunt"].status == "todo" and items["Product Hunt"].links_to_you and "sameAs" in items["Product Hunt"].action
    assert items["G2"].status == "done" and items["G2"].evidence_ref == g2
    assert items["Capterra"].status == "todo" and items["Capterra"].action.endswith("Pick the recruiting category.")
    assert items["Capterra"].evidence_ref == home["id"], "a missing listing rests on the crawl that shows it missing"


def test_prospects_link_to_two_competitors_and_skip_spam_namesakes_generic_and_existing_links():
    rds = []
    for comp in ("naukri.com", "iimjobs.com", "instahyre.com"):
        rds += [_rd("competitor", comp, "www.yourstory.com", rank=64), _rd("competitor", comp, "spam.biz", spam=80),
                _rd("competitor", comp, "korum.in"), _rd("competitor", comp, "youtube.com"), _rd("competitor", comp, "g2.com"),
                _rd("competitor", comp, "hrkatha.com"), _rd("competitor", comp, "worldhire.com")]
    rds += [_rd("competitor", "naukri.com", "inc42.com", rank=70), _rd("competitor", "naukri.com", "lonely.example"),
            _rd("competitor", "iimjobs.com", "inc42.com", rank=40), _rd("competitor", "iimjobs.com", "gone.example", lost=datetime(2026, 1, 1, tzinfo=UTC)),
            _rd("competitor", "naukri.com", "gone.example")]
    out = prospects(rds, own_hosts={"worldhire.com", "korum.worldhire.com"}, competitors={"naukri.com", "iimjobs.com", "instahyre.com"},
                    target_domains={"g2.com"}, linking={"hrkatha.com"}, brand=BRAND)
    assert [(p.url, p.competitors, p.domain_rank) for p in out] == [("https://yourstory.com/", 3, 64), ("https://inc42.com/", 2, 70)]
    assert out[1].competitor_urls == ["https://iimjobs.com/", "https://naukri.com/"]


def test_anchors_are_bucketed_and_exact_match_over_five_percent_is_flagged():
    kws = {"confidential job search", "executive search firm"}
    assert anchor_bucket("KORUM", BRAND, kws) == "branded"
    assert anchor_bucket("korum.worldhire.com", BRAND, kws) == "naked_url"
    assert anchor_bucket("Click here", BRAND, kws) == "generic"
    assert anchor_bucket("confidential job search", BRAND, kws) == "exact_match"
    assert anchor_bucket("tips for a confidential job search", BRAND, kws) == "partial_match"
    assert anchor_bucket("", BRAND, kws) == "other"
    rows = [{"id": _id(), "anchor": "KORUM", "backlinks": 8}, {"id": _id(), "anchor": "confidential job search", "backlinks": 2},
            {"id": _id(), "anchor": "lost one", "backlinks": 50, "lost_date": datetime(2026, 1, 1, tzinfo=UTC)}]
    shares, total = anchor_shares(rows, BRAND, kws)
    assert total == 10
    by = {a.bucket: a for a in shares}
    assert by["branded"].share_pct == 80.0 and by["branded"].within is False
    assert by["exact_match"].share_pct == 20.0 and by["exact_match"].within is False
    project = _project()
    home = _home()
    out = run_linkbuilding(AnalystContext(llm=FakeLLM({"linkbuilding": lambda s, u, sc: {"prospects": []}}), model="fake"),
                           AnalystInput(project=project, run_id=_id(), params={"today": TODAY.isoformat()},
                                        rows={"raw_anchors": rows, "keywords": [{"keyword": k} for k in kws], "raw_crawl_pages": [home]}))
    assert [f.kind for f in out.flags] == ["exact_match_anchors"]


def test_velocity_counts_new_domains_by_month_and_flags_a_spike():
    rds = [_rd("own", "korum.worldhire.com", f"s{i}.example", first_seen=datetime(2026, 9, 2, tzinfo=UTC)) for i in range(55)]
    rds.append(_rd("own", "korum.worldhire.com", "early.example", first_seen=datetime(2026, 5, 20, tzinfo=UTC)))
    vel, flags = velocity(rds, rds[0]["id"], TODAY)
    months = {m.month: m.new_domains for m in vel.months}
    assert len(vel.months) == 12 and vel.months[-1].month == "2026-10" and vel.months[0].month == "2025-11"
    assert months["2026-09"] == 55 and months["2026-05"] == 1 and vel.last_month_new == 55
    assert vel.program_month == 6 and "8 to 15" in vel.guideline
    assert [f.kind for f in flags] == ["velocity_spike"]
    assert velocity([], None, TODAY) == (None, []), "no rows, no velocity: null, not zeroes"


def test_tactics_follow_the_phase_and_foundation_comes_back_while_profiles_are_missing():
    home = _home()
    targets = [{"platform": p, "domain": f"{p}.com", "kind": "profile"} for p in ("a", "b", "c", "d")] + [{"platform": "dir", "domain": "dir.com", "kind": "directory"}]
    todo = checklist(targets, {}, {}, home["id"], "KORUM", "korum.worldhire.com")
    gap = prospects([_rd("competitor", c, "yourstory.com") for c in ("naukri.com", "iimjobs.com")], set(), {"naukri.com", "iimjobs.com"}, set(), set(), BRAND)
    assert choose_tactics("foundation", todo, gap) == ["entity_stacking", "citations", "competitor_gap"]
    assert choose_tactics("growth", todo, gap) == ["entity_stacking", "competitor_gap", "guest_posting"]
    done = checklist(targets, {f"{p}.com": home["id"] for p in "abcd"}, {"dir.com": _id()}, home["id"], "KORUM", "korum.worldhire.com")
    assert choose_tactics("foundation", done, []) == ["guest_posting"], "nothing left to list and no gap: only the next tactic"
    assert choose_tactics("authority", done, gap) == ["partnerships", "podcasts", "skyscraper"]


def test_model_text_is_checked_in_code():
    facts = "KORUM serves India, the UK and the US."
    assert clean_copy("KORUM is a confidential hiring platform.", 50, facts, "KORUM", RULES)
    assert clean_copy("KORUM helps 10,000 professionals.", 50, facts, "KORUM", RULES) is None, "a number it was not given"
    assert clean_copy("KORUM, the AI-powered platform.", 50, facts, "KORUM", RULES) is None, "a blocking brand rule"
    assert clean_copy("A confidential hiring platform.", 50, facts, "KORUM", RULES) is None, "the brand name is missing"
    assert clean_copy("KORUM " + "word " * 60, 50, facts, "KORUM", RULES) is None, "over length"

    project = _project()
    home = _home()
    rds = [_rd("competitor", c, d) for c in ("naukri.com", "iimjobs.com") for d in ("yourstory.com", "inc42.com", "hrkatha.com")]
    seen = {}

    def reply(system, user, schema):
        seen.update(json.loads(user))
        return {"prospects": [
            {"domain": "yourstory.com", "category": "news", "approach": "Offer yourstory.com a founder story on hiring senior people in private."},
            {"domain": "inc42.com", "category": "blog", "approach": "Pitch inc42.com 3 data points."},
            {"domain": "hrkatha.com", "category": "magazine", "approach": "Share a hand-picked list of tools."},
            {"domain": "made-up.example", "category": "directory", "approach": "List the site."}],
            "profile_copy": {"short": "KORUM is a confidential hiring platform for senior professionals in India, the UK and the US.",
                             "medium": "KORUM — private hiring.", "long": None}}

    out = run_linkbuilding(AnalystContext(llm=FakeLLM({"linkbuilding": reply}), model="fake"),
                           AnalystInput(project=project, run_id=_id(), params={"today": TODAY.isoformat()},
                                        rows={"raw_referring_domains": rds, "raw_crawl_pages": [home], "brand_rules": RULES}))
    assert [d["domain"] for d in seen["prospects"]] == ["hrkatha.com", "inc42.com", "yourstory.com"]
    assert "consent-led" in seen["facts"], "the descriptions are written from the project's own description"
    by = {p.url: p for p in out.prospects}
    assert set(by) == {"https://yourstory.com/", "https://inc42.com/", "https://hrkatha.com/"}, "a domain the model made up is ignored"
    assert by["https://yourstory.com/"].approach and by["https://yourstory.com/"].category == "news"
    assert by["https://inc42.com/"].approach is None, "numbers in an approach are dropped"
    assert by["https://hrkatha.com/"].category is None and by["https://hrkatha.com/"].approach is None
    assert out.profile_copy and out.profile_copy.short and out.profile_copy.medium is None, "the em dash breaks a block rule"
    assert out.profile_copy.evidence_ref == home["id"]


def test_no_model_call_without_prospects_or_a_homepage():
    llm = FakeLLM({})
    out = run_linkbuilding(AnalystContext(llm=llm, model="fake"), AnalystInput(project=_project(), run_id=_id(), rows={}))
    assert llm.calls == [] and out.phase is None and out.tactics == [] and out.velocity is None


# ---------- end to end: gate and persistence ----------

def _seed_raw(worker_url, project_id, run_id, rows: dict[str, list[dict]]):
    with project_scope(project_id, caller="collector:backlink_gap", url=worker_url) as s:
        for table, items in rows.items():
            for r in items:
                s.insert(table, {"run_id": run_id, **r})


@pytest.mark.db
@requires_db
def test_link_plan_passes_the_gate_persists_and_marks_won_prospects(worker_url, seeded):
    from orchestrator.gate_runner import analyse_and_gate
    from orchestrator.runtime import Runtime

    with project_scope(seeded["korum"], url=worker_url) as s:
        project = Project.from_row(s.fetchone("select * from projects where id = %(project_id)s"))
        assert s.fetchone("select count(*) as n from link_targets where project_id = %(project_id)s")["n"] == len(KORUM.link_targets)
    run1 = uuid.uuid4()
    seen = datetime(2026, 9, 3, tzinfo=UTC)
    _seed_raw(worker_url, project.id, run1, {
        "raw_backlink_summary": [{"target": "korum.worldhire.com", "role": "own", "rank": 3, "referring_domains": 1, "first_seen": seen},
                                 {"target": "naukri.com", "role": "competitor", "rank": 78, "referring_domains": 40000}],
        "raw_referring_domains": [{"target": "korum.worldhire.com", "role": "own", "domain": "g2.com", "rank": 80, "first_seen": seen}]
        + [{"target": c, "role": "competitor", "domain": d, "rank": 60} for c in ("naukri.com", "iimjobs.com") for d in ("yourstory.com", "inc42.com")],
        "raw_anchors": [{"target": "korum.worldhire.com", "anchor": "KORUM", "backlinks": 1}],
    })
    with project_scope(project.id, caller="collector:site_crawl", url=worker_url) as s:
        home = _home(["https://www.linkedin.com/company/korum"])
        s.insert("raw_crawl_pages", {"run_id": run1, **{k: v for k, v in home.items() if k not in ("id", "raw_jsonld")}, "raw_jsonld": jsonb(home["raw_jsonld"])})

    def reply(system, user, schema):
        return {"prospects": [{"domain": d["domain"], "category": "news", "approach": "Offer a founder story on private hiring."} for d in json.loads(user)["prospects"]],
                "profile_copy": {"short": "KORUM is a confidential hiring platform for senior professionals."}}

    rt = Runtime(db_url=worker_url, llm=FakeLLM({"linkbuilding": reply}), model="fake")
    res = analyse_and_gate(rt, project, run1, "linkbuilding", {"today": TODAY.isoformat()})
    assert not res["blocked"], res["violations"]
    with project_scope(project.id, url=worker_url) as s:
        plan = s.fetchone("select * from link_plans where project_id = %(project_id)s and run_id = %(r)s", {"r": run1})
        opps = {(o["kind"], o["domain"]): o for o in s.fetchall("select * from link_opportunities where project_id = %(project_id)s")}
    assert plan["phase"] == "foundation" and [t["slug"] for t in plan["tactics"]][0] == "entity_stacking"
    assert plan["profile_copy"]["short"].startswith("KORUM")
    assert opps[("profile", "linkedin.com")]["status"] == "done" and opps[("profile", "linkedin.com")]["done_at"] is not None
    assert opps[("review", "g2.com")]["status"] == "done"
    assert opps[("profile", "crunchbase.com")]["status"] == "todo" and opps[("profile", "crunchbase.com")]["done_at"] is None
    assert opps[("prospect", "yourstory.com")]["status"] == "todo" and opps[("prospect", "yourstory.com")]["competitor_domains"] == ["iimjobs.com", "naukri.com"]

    # next month yourstory.com links to KORUM: it is no longer a gap, and the earlier prospect is won
    run2 = uuid.uuid4()
    _seed_raw(worker_url, project.id, run2, {
        "raw_referring_domains": [{"target": "korum.worldhire.com", "role": "own", "domain": d, "rank": 60, "first_seen": seen} for d in ("g2.com", "yourstory.com")]
        + [{"target": c, "role": "competitor", "domain": d, "rank": 60} for c in ("naukri.com", "iimjobs.com") for d in ("yourstory.com", "inc42.com")]})
    res = analyse_and_gate(rt, project, run2, "linkbuilding", {"today": TODAY.isoformat()})
    assert not res["blocked"], res["violations"]
    with project_scope(project.id, url=worker_url) as s:
        won = s.fetchone("select status, done_at from link_opportunities where project_id = %(project_id)s and kind = 'prospect' and domain = 'yourstory.com'")
        still = s.fetchone("select status, last_run_id from link_opportunities where project_id = %(project_id)s and kind = 'prospect' and domain = 'inc42.com'")
    assert won["status"] == "won" and won["done_at"] is not None
    assert still["status"] == "todo" and still["last_run_id"] == run2
