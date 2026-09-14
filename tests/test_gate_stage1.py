"""M3 acceptance: stage 1 blocks an em dash, a leaked /recruiter/inbox, and a dangling evidence_ref on
Korum's rule set; the em dash rule alone applies on RejuveLuxe's."""
import uuid

from config.loader import load_all_project_configs
from gate.stage1_rules import ProjectRules, run_stage1

CFG = {c.slug: c for c in load_all_project_configs()}


def rules_for(slug: str) -> ProjectRules:
    c = CFG[slug]
    return ProjectRules(
        brand_rules=[{"rule_type": r.rule_type, "pattern": r.pattern, "severity": r.severity, "active": True} for r in c.brand_rules],
        critical_rules=[{"rule_key": r.rule_key, "url_pattern": r.url_pattern, "assertion": r.assertion, "active": True} for r in c.critical_rules],
        slug=slug,
    )


KORUM = rules_for("korum")
REJUVE = rules_for("rejuveluxe")
REF = uuid.uuid4()
RESOLVE_ALL = lambda refs: {REF}  # noqa: E731


def onpage(suggested: str, url: str = "https://korum.worldhire.com/about", ref=REF):
    return {"agent": "onpage", "suggestions": [{"evidence_ref": str(ref) if ref else None, "url": url, "field": "title",
                                                "current": "About", "suggested": suggested, "rationale": "shorter"}]}


def test_em_dash_blocked_on_korum_and_rejuveluxe():
    art = onpage("Hiring — done right")
    for rules in (KORUM, REJUVE):
        r = run_stage1("onpage", art, rules, RESOLVE_ALL)
        assert r.blocked and any(v.check == "brand" and "—" in v.detail for v in r.violations)


def test_en_dash_and_hyphen_pass():
    for text in ("Hiring – done right", "Hiring - done right"):
        assert not run_stage1("onpage", onpage(text), KORUM, RESOLVE_ALL).blocked


def test_leaked_auth_path_blocked_on_korum_only():
    art = onpage("Check your inbox", url="https://korum.worldhire.com/recruiter/inbox")
    r = run_stage1("onpage", art, KORUM, RESOLVE_ALL)
    assert r.blocked and any(v.check == "url_allowlist" and "no_indexable_auth_route" in v.detail for v in r.violations)
    # rejuveluxe has no recruiter rule, but has a checkout rule
    assert not run_stage1("onpage", art, REJUVE, RESOLVE_ALL).blocked
    cart = onpage("Your cart", url="https://rejuveluxe.com/cart?step=2")
    assert run_stage1("onpage", cart, REJUVE, RESOLVE_ALL).blocked


def test_auth_path_inside_prose_is_caught():
    art = onpage("See /dashboard/settings for details")
    assert run_stage1("onpage", art, KORUM, RESOLVE_ALL).blocked
    art = onpage("See https://korum.worldhire.com/admin/users for details")
    assert run_stage1("onpage", art, KORUM, RESOLVE_ALL).blocked


def test_dangling_evidence_ref_blocked():
    art = onpage("Fine title")
    r = run_stage1("onpage", art, KORUM, lambda refs: set())
    assert r.blocked and any(v.check == "evidence" and v.severity == "block" for v in r.violations)


def test_assertion_without_evidence_is_dropped_not_blocked():
    art = onpage("Fine title", ref=None)
    r = run_stage1("onpage", art, KORUM, RESOLVE_ALL)
    assert not r.blocked and r.dropped == 1 and r.artifact["suggestions"] == []


def test_brand_rules_are_not_portable():
    assert run_stage1("onpage", onpage("Curated roles for you"), KORUM, RESOLVE_ALL).blocked
    assert not run_stage1("onpage", onpage("Curated skincare for you"), REJUVE, RESOLVE_ALL).blocked
    # near miss: "curation" is not "curated"
    assert not run_stage1("onpage", onpage("Careful curation of roles"), KORUM, RESOLVE_ALL).blocked
    assert run_stage1("onpage", onpage("Every Candidate wins"), KORUM, RESOLVE_ALL).blocked
    assert not run_stage1("onpage", onpage("Every Job Seeker wins"), KORUM, RESOLVE_ALL).blocked
    assert run_stage1("onpage", onpage("you will never feel invisible"), KORUM, RESOLVE_ALL).blocked


def test_quoted_evidence_field_is_not_brand_swept():
    art = onpage("Fine title")
    art["suggestions"][0]["current"] = "Old title — with em dash from the crawl"
    assert not run_stage1("onpage", art, KORUM, RESOLVE_ALL).blocked


def test_schema_violation_blocks():
    r = run_stage1("onpage", {"agent": "onpage", "suggestions": [{"url": "x", "field": "bogus", "suggested": "t", "rationale": "r"}]}, KORUM, RESOLVE_ALL)
    assert r.blocked and r.violations[0].check == "schema"
    r = run_stage1("nope", {}, KORUM, RESOLVE_ALL)
    assert r.blocked


def test_numeric_sanity():
    art = {"agent": "keyword", "keywords": [{"evidence_ref": str(REF), "keyword": "x", "volume": 5_000_000_000}]}
    r = run_stage1("keyword", art, KORUM, RESOLVE_ALL)
    assert r.blocked and any(v.check == "numeric" for v in r.violations)
    art = {"agent": "trend", "events": [{"evidence_ref": str(REF), "kind": "algorithm_update", "name": "x", "source_url": "https://status.search.google.com/", "observed_on": "1999-01-01", "detail": "d"}]}
    assert run_stage1("trend", art, KORUM, RESOLVE_ALL).blocked
