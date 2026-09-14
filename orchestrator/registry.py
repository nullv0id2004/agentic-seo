"""Agent registry: every agent declares its reads, writes and whether it may touch the network or a model.

The runtime enforces the declaration. A collector gets a scope that can write raw_* tables and nothing
else. An analyst gets no scope at all: the runtime reads its declared tables and hands it rows.
Executors get a scope limited to the tables they mark and one credential each.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentSpec:
    name: str
    kind: str                                    # 'collector' | 'analyst' | 'gate' | 'executor' | 'control'
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    network: bool = False
    llm: bool = False
    credentials: tuple[str, ...] = field(default_factory=tuple)   # names of secrets it may resolve

    @property
    def caller(self) -> str:
        return f"{self.kind}:{self.name}"


AGENTS: dict[str, AgentSpec] = {}


def register(spec: AgentSpec) -> AgentSpec:
    if spec.kind == "analyst" and spec.network:
        raise ValueError(f"{spec.name}: analysts never have network (Section 13.2)")
    if spec.kind == "collector" and spec.llm:
        raise ValueError(f"{spec.name}: collectors never call an LLM (Section 13.2)")
    if spec.kind != "collector" and any(t.startswith("raw_") for t in spec.writes):
        raise ValueError(f"{spec.name}: only collectors write raw_* tables (Section 13.4)")
    AGENTS[spec.name] = spec
    return spec


for _c, _writes, _reads in [
    ("gsc_performance", ("raw_gsc_performance",), ()),
    ("gsc_inspection", ("raw_crawl_pages", "audit_log"), ("raw_crawl_pages", "audit_log")),
    ("ga4", ("raw_ga4_daily",), ()),
    ("site_crawl", ("raw_crawl_pages", "raw_sitemap_urls"), ()),
    ("header_probe", ("raw_crawl_pages", "audit_log"), ("critical_rules",)),
    ("vitals", ("raw_vitals",), ()),
    ("serp", ("raw_serp",), ("keywords",)),
    ("keyword_metrics", ("raw_keyword_metrics",), ("keywords",)),
    ("backlinks", ("mentions",), ()),
    ("doc_fetch", ("raw_fetched_documents",), ()),
    ("search_status", ("raw_search_status",), ()),
]:
    register(AgentSpec(_c, "collector", reads=_reads, writes=_writes + ("collection_gaps",), network=True))

register(AgentSpec("technical", "analyst", reads=("raw_crawl_pages", "raw_vitals", "raw_sitemap_urls", "critical_rules"), llm=True))
register(AgentSpec("ecommerce", "analyst", reads=("raw_crawl_pages", "critical_rules"), llm=True))
register(AgentSpec("keyword", "analyst", reads=("raw_keyword_metrics", "raw_gsc_performance", "keywords", "critical_rules"), llm=True))
register(AgentSpec("onpage", "analyst", reads=("raw_crawl_pages", "keywords", "brand_rules"), llm=True))
register(AgentSpec("content", "analyst", reads=("keywords", "raw_fetched_documents", "content_briefs"), llm=True))
register(AgentSpec("offpage", "analyst", reads=("mentions", "raw_serp"), llm=True))
register(AgentSpec("trend", "analyst", reads=("raw_serp", "mentions", "raw_search_status"), llm=True))
register(AgentSpec("report", "analyst", reads=("raw_gsc_performance", "raw_ga4_daily", "issues", "raw_serp", "mentions", "collection_gaps", "runs"), llm=True))
register(AgentSpec("gate_stage1", "gate", reads=("brand_rules", "critical_rules"), writes=("gate_results",)))
register(AgentSpec("gate_stage2", "gate", reads=("raw_fetched_documents",), writes=("gate_results",), llm=True))
register(AgentSpec("github", "executor", reads=("approvals",), writes=("approvals", "audit_log"), network=True, credentials=("github-fix-branch-token",)))
register(AgentSpec("cms", "executor", reads=("approvals", "content_briefs"), writes=("approvals", "content_briefs", "audit_log"), network=True, credentials=("cms-content-write-token",)))
register(AgentSpec("email", "executor", reads=("approvals", "pitches"), writes=("approvals", "pitches", "audit_log"), network=True, credentials=("outreach-smtp",)))
register(AgentSpec("orchestrator", "control", writes=("runs", "issues", "approvals", "agent_logs", "audit_log", "keywords", "pages",
                                                       "content_briefs", "pitches", "reports", "gate_results", "mentions")))


def spec(name: str) -> AgentSpec:
    return AGENTS[name]


ALWAYS_WRITABLE = frozenset({"agent_logs", "audit_log", "collection_gaps"})   # every agent logs; nothing else is implicit


def grants(name: str) -> frozenset[str]:
    return frozenset(spec(name).writes) | ALWAYS_WRITABLE
