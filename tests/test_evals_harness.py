"""Section 14 thresholds, enforced in CI in deterministic mode."""
import json
from pathlib import Path

from evals.adversarial.generate import cases as generate_cases
from evals.run_evals import CASES, THRESHOLDS, GullibleFake, evaluate


def test_fixture_files_match_generator():
    on_disk = {p.name: json.loads(p.read_text()) for p in CASES.glob("*.json")}
    generated = {f"{c['id']}.json": c for c in generate_cases()}
    assert on_disk == generated, "run evals/adversarial/generate.py and commit"


def test_fixture_counts():
    by_cat = {}
    for c in generate_cases():
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    assert by_cat["brand"] == 60 and by_cat["confidentiality"] == 10 and by_cat["fabricated_stat"] == 15
    assert by_cat["injection"] == 5 and by_cat["dangling_ref"] == 5 and by_cat["clean"] >= 40


def test_thresholds_hold_with_gullible_verifier():
    cases = [json.loads(p.read_text()) for p in sorted(Path(CASES).glob("*.json"))]
    rates, failures = evaluate(cases, GullibleFake(), live=False)
    assert not failures, failures
    for cat in ("confidentiality", "brand", "injection", "dangling_ref"):
        assert rates[cat][0] == 1.0
    assert rates["fabricated_stat"][0] >= THRESHOLDS["fabricated_stat"]
    assert 1 - rates["clean"][0] <= THRESHOLDS["clean_false_positive_max"]
