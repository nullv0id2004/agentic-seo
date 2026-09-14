#!/usr/bin/env python3
"""Seed projects, brand_rules and critical_rules from config/projects/*.yaml. Idempotent; operator action.

  SEO_DATABASE_URL=... python scripts/seed_projects.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.loader import seed_projects  # noqa: E402

if __name__ == "__main__":
    url = os.environ.get("SEO_DATABASE_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not url:
        print("SEO_DATABASE_URL is required")
        sys.exit(2)
    for slug, pid in seed_projects(url).items():
        print(f"{slug}: {pid}")
