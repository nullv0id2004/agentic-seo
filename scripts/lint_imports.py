#!/usr/bin/env python3
"""P1 enforcement. collectors/ may not import an LLM SDK; analysts/ and gate/stage2 may not import a network client.

Exit 1 with a list of offending imports. Run in CI on every push.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LLM_MODULES = {"anthropic", "openai", "langchain", "langchain_anthropic", "langchain_openai", "litellm", "google.generativeai", "llm"}
NETWORK_MODULES = {"httpx", "requests", "playwright", "aiohttp", "urllib.request", "urllib3", "socket", "http.client", "selenium", "websockets"}

RULES = {
    "collectors": LLM_MODULES,
    "analysts": NETWORK_MODULES | {"subprocess"},
    "gate": NETWORK_MODULES | {"subprocess"},
}


def imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((node.lineno, a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.lineno, node.module))
    return out


def violates(mod: str, banned: set[str]) -> bool:
    return any(mod == b or mod.startswith(b + ".") for b in banned)


def main() -> int:
    failures: list[str] = []
    for package, banned in RULES.items():
        for path in (ROOT / package).rglob("*.py"):
            for lineno, mod in imported_modules(path):
                if violates(mod, banned):
                    failures.append(f"{path.relative_to(ROOT)}:{lineno}: {package}/ may not import {mod}")
    # llm.py is the only module allowed to import the SDK; nothing outside analysts/, gate/, executors may use it.
    for path in (ROOT / "collectors").rglob("*.py"):
        for lineno, mod in imported_modules(path):
            if mod == "llm" or mod.startswith("llm."):
                failures.append(f"{path.relative_to(ROOT)}:{lineno}: collectors/ may not import the llm client")
    if failures:
        print("\n".join(failures))
        return 1
    print("import lint: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
