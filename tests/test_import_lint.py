"""M1 acceptance: the import lint fails a deliberately added bad import."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINT = ROOT / "scripts" / "lint_imports.py"


def run_lint() -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(LINT)], capture_output=True, text=True, cwd=ROOT)


def test_lint_passes_on_clean_tree():
    r = run_lint()
    assert r.returncode == 0, r.stdout + r.stderr


def test_lint_fails_on_llm_import_in_collectors():
    bad = ROOT / "collectors" / "_zz_bad_import.py"
    bad.write_text("import anthropic\n")
    try:
        r = run_lint()
    finally:
        bad.unlink()
    assert r.returncode == 1
    assert "collectors/ may not import anthropic" in r.stdout


def test_lint_fails_on_network_import_in_analysts():
    bad = ROOT / "analysts" / "_zz_bad_import.py"
    bad.write_text("from httpx import Client\n")
    try:
        r = run_lint()
    finally:
        bad.unlink()
    assert r.returncode == 1
    assert "analysts/ may not import httpx" in r.stdout
