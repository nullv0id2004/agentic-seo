import pytest
from pydantic import ValidationError

from config.loader import load_all_project_configs
from contracts.project import ProjectConfig


def test_three_projects_load_and_no_cruise_guru():
    cfgs = load_all_project_configs()
    slugs = {c.slug for c in cfgs}
    assert slugs == {"korum", "worldhire", "rejuveluxe"}
    assert not any("cruise" in s for s in slugs)


def test_rules_are_per_project_not_portable():
    by = {c.slug: c for c in load_all_project_configs()}
    korum_patterns = {r.pattern for r in by["korum"].brand_rules}
    rl_patterns = {r.pattern for r in by["rejuveluxe"].brand_rules}
    assert any("curated" in p for p in korum_patterns)
    assert not any("curated" in p for p in rl_patterns)
    assert "—" in korum_patterns and "—" in rl_patterns
    assert {c.rule_key for c in by["korum"].critical_rules} >= {"no_jobposting_schema"}
    assert "no_jobposting_schema" not in {c.rule_key for c in by["worldhire"].critical_rules}


def test_cruise_guru_rejected():
    with pytest.raises(ValidationError):
        ProjectConfig(slug="cruise-guru", vertical="content", domains=["x"], approver_id="00000000-0000-4000-8000-000000000001")


def test_content_cap_cannot_be_raised_to_mass_generation():
    with pytest.raises(ValidationError):
        ProjectConfig(slug="x", vertical="content", domains=["x"], approver_id="00000000-0000-4000-8000-000000000001",
                      monthly_content_cap=50)
