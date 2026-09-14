"""Name -> collector body. The orchestrator dispatches by name and never imports collector internals."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from collectors import (
    backlinks,
    crawl,
    doc_fetch,
    ga4,
    gsc,
    header_probe,
    keyword_metrics,
    psi,
    search_status,
    serp,
)
from collectors.base import CollectFn, CollectorResult, run_collector
from contracts.project import Project

COLLECTORS: dict[str, CollectFn] = {
    "gsc_performance": gsc.collect_performance,
    "gsc_inspection": gsc.collect_inspection,
    "ga4": ga4.collect,
    "site_crawl": crawl.collect,
    "header_probe": header_probe.collect,
    "vitals": psi.collect,
    "serp": serp.collect,
    "doc_fetch": doc_fetch.collect,
    "search_status": search_status.collect,
    "keyword_metrics": keyword_metrics.collect,
    "backlinks": backlinks.collect,
}


def register(name: str, fn: CollectFn) -> None:
    COLLECTORS[name] = fn


def collect(name: str, project: Project, run_id: UUID, params: dict[str, Any] | None = None,
            db_url: str | None = None) -> CollectorResult:
    if name not in COLLECTORS:
        raise KeyError(f"unknown collector {name!r}")
    return run_collector(name, COLLECTORS[name], project, run_id, params, db_url)
