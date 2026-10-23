"""Persist gated artifacts into derived tables, and run analyst -> stage 1 -> stage 2 -> persist as one step."""
from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from analysts.technical import INDEX_ISSUE_TYPES
from db.connection import ProjectScope, jsonb
from orchestrator.runtime import Runtime

# issue types that rest on raw_crawl_pages alone; an audit that crawled a URL and did not emit one of
# these for it has shown the issue is gone. Vitals-based types are not here: a run without raw_vitals
# says nothing about LCP.
CRAWL_ISSUE_TYPES = frozenset({
    "critical_rule_violation", "disallowed_schema_type", "canonical_loop", "orphaned_pillar_page", "redirect_chain",
    "broken_internal_links", "duplicate_title", "sitemap_url_not_200", "missing_title", "missing_meta_description",
    "missing_h1", "multiple_h1", "thin_content", "missing_canonical",
    "offer_schema_incomplete", "out_of_stock_without_handling", "product_price_matches_schema", "variant_canonical",
})
# Vitals issues close when this run measured the same page the same way (strategy, lab or field, page or origin)
# and got a value for the metric without flagging it again. The issue's evidence_ref is the raw_vitals row it was
# raised on; a run that did not measure that page says nothing about it.
VITALS_ISSUE_TYPES = frozenset({"lcp_slow", "inp_slow", "cls_high"})


def issue_fingerprint(issue: dict[str, Any]) -> str:
    """Identity of an issue across runs. Must match the backfill expression in migration 0005."""
    key = issue.get("url") or issue["evidence"]
    return hashlib.sha256(f"{issue['issue_type']}|{key}".encode()).hexdigest()


def write_issues(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> list[UUID]:
    """Upsert one row per (project, fingerprint). A recurring issue keeps its id and first run_id, gets
    the latest evidence and fix text, and is reopened if it had been resolved. A dismissed issue stays
    dismissed: the operator said so. Returns ids in artifact order so callers can zip them with issues."""
    ids: list[UUID] = []
    for issue in artifact.get("issues", []):
        row = scope.fetchone(
            """insert into issues (project_id, run_id, last_run_id, fingerprint, url, issue_type, severity, evidence, evidence_ref,
                                   recommended_fix, claude_code_prompt)
               values (%(project_id)s, %(run_id)s, %(run_id)s, %(fp)s, %(url)s, %(issue_type)s, %(severity)s, %(evidence)s, %(evidence_ref)s,
                       %(recommended_fix)s, %(claude_code_prompt)s)
               on conflict (project_id, fingerprint) do update set
                 last_run_id = excluded.last_run_id, last_seen_at = now(), seen_count = issues.seen_count + 1,
                 severity = excluded.severity, evidence = excluded.evidence, evidence_ref = excluded.evidence_ref,
                 recommended_fix = excluded.recommended_fix, claude_code_prompt = excluded.claude_code_prompt,
                 status = case when issues.status = 'resolved' then 'open' else issues.status end,
                 resolved_at = case when issues.status = 'resolved' then null else issues.resolved_at end
               returning id""",
            {"run_id": run_id, "fp": issue_fingerprint(issue), "url": issue.get("url"), "issue_type": issue["issue_type"],
             "severity": issue["severity"], "evidence": issue["evidence"], "evidence_ref": issue.get("evidence_ref"),
             "recommended_fix": issue.get("recommended_fix"), "claude_code_prompt": issue.get("claude_code_prompt")},
        )
        ids.append(row["id"])
    return ids


def resolve_unseen_issues(scope: ProjectScope, run_id: UUID, artifacts: list[dict[str, Any]]) -> int:
    """Close open crawl-based issues on URLs this run crawled but did not flag again. Deterministic: the
    raw_crawl_pages rows of this run are the evidence that the page was looked at."""
    seen = [issue_fingerprint(i) for a in artifacts for i in a.get("issues", [])]
    crawled = [r["url"] for r in scope.fetchall(
        "select url from raw_crawl_pages where project_id = %(project_id)s and run_id = %(run_id)s and status_code is not null", {"run_id": run_id})]
    cur = scope.execute(
        """update issues set status = 'resolved', resolved_at = now()
            where project_id = %(project_id)s and status = 'open' and issue_type = any(%(types)s)
              and url = any(%(crawled)s) and not (fingerprint = any(%(seen)s))""",
        {"types": sorted(CRAWL_ISSUE_TYPES), "crawled": crawled, "seen": seen},
    )
    n = cur.rowcount
    # Index issues close only on a fresh URL Inspection verdict: a crawl alone says nothing about Google.
    inspected = [r["url"] for r in scope.fetchall(
        "select url from raw_url_inspection where project_id = %(project_id)s and run_id = %(run_id)s", {"run_id": run_id})]
    if inspected:
        cur = scope.execute(
            """update issues set status = 'resolved', resolved_at = now()
                where project_id = %(project_id)s and status = 'open' and issue_type = any(%(types)s)
                  and url = any(%(inspected)s) and not (fingerprint = any(%(seen)s))""",
            {"types": sorted(INDEX_ISSUE_TYPES), "inspected": inspected, "seen": seen},
        )
        n += cur.rowcount
    # Two statements: the scope refuses a write that names a raw_* table, so the read finds them first.
    remeasured = [r["id"] for r in scope.fetchall(
        """select i.id from issues i join raw_vitals was on was.project_id = i.project_id and was.id = i.evidence_ref
            where i.project_id = %(project_id)s and i.status = 'open' and i.issue_type = any(%(types)s) and not (i.fingerprint = any(%(seen)s))
              and exists (select 1 from raw_vitals cur
                           where cur.project_id = was.project_id and cur.run_id = %(run_id)s and cur.url = was.url
                             and cur.strategy is not distinct from was.strategy and cur.source is not distinct from was.source
                             and coalesce(cur.field_scope, 'url') = coalesce(was.field_scope, 'url')
                             and case i.issue_type when 'lcp_slow' then cur.lcp_ms when 'inp_slow' then cur.inp_ms else cur.cls end is not null)""",
        {"types": sorted(VITALS_ISSUE_TYPES), "seen": seen, "run_id": run_id})]
    if remeasured:
        cur = scope.execute("update issues set status = 'resolved', resolved_at = now() where project_id = %(project_id)s and id = any(%(ids)s)",
                            {"ids": remeasured})
        n += cur.rowcount
    if n:
        scope.audit("orchestrator", "issues_resolved", {"count": n}, run_id)
    return n


def write_trend_events(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> list[UUID]:
    """One row per (project, kind, source_url) across runs; a recurring event keeps its id and first
    run_id and takes the latest detail. Returns ids in artifact order."""
    ids: list[UUID] = []
    for e in artifact.get("events", []):
        row = scope.fetchone(
            """insert into trend_events (project_id, run_id, last_run_id, kind, name, source_url, observed_on, detail, evidence_ref)
               values (%(project_id)s, %(run_id)s, %(run_id)s, %(kind)s, %(name)s, %(source_url)s, %(observed_on)s, %(detail)s, %(evidence_ref)s)
               on conflict (project_id, kind, source_url) do update set
                 last_run_id = excluded.last_run_id, last_seen_at = now(), seen_count = trend_events.seen_count + 1,
                 name = excluded.name, observed_on = excluded.observed_on, detail = excluded.detail, evidence_ref = excluded.evidence_ref
               returning id""",
            {"run_id": run_id, "kind": e["kind"], "name": e["name"], "source_url": e["source_url"], "observed_on": e["observed_on"],
             "detail": e.get("detail"), "evidence_ref": e.get("evidence_ref")})
        ids.append(row["id"])
    return ids


def write_report(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> UUID:
    metrics = {m["name"]: {k: v for k, v in m.items() if k != "name"} for m in artifact.get("metrics", [])}
    metrics["cutoff_date"] = artifact.get("cutoff_date")
    return scope.insert("reports", {
        "run_id": run_id, "period": artifact["period"], "metrics": metrics, "commentary": artifact.get("commentary"),
        "caveats": jsonb(artifact.get("caveats", [])),
    })


def write_keywords(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> list[UUID]:
    """Upsert keyword rows. Mapping changes are proposals: they land in keywords only after approval,
    so here we store intent, cluster, flags and the latest metric id, and leave mapped_url untouched."""
    ids = []
    for k in artifact.get("keywords", []):
        row = scope.fetchone(
            """insert into keywords (project_id, keyword, intent, cluster, blocked_for_index, latest_metric_id, updated_at)
               values (%(project_id)s, %(keyword)s, %(intent)s, %(cluster)s, %(blocked)s, %(metric)s, now())
               on conflict (project_id, keyword) do update set intent = coalesce(excluded.intent, keywords.intent),
                 cluster = coalesce(excluded.cluster, keywords.cluster), blocked_for_index = excluded.blocked_for_index,
                 latest_metric_id = excluded.latest_metric_id, updated_at = now()
               returning id""",
            {"keyword": k["keyword"], "intent": k.get("intent"), "cluster": k.get("cluster"), "blocked": k.get("blocked_for_index", False), "metric": k.get("evidence_ref")},
        )
        ids.append(row["id"])
    return ids


def write_onpage(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> list[UUID]:
    return [scope.insert("onpage_suggestions", {
        "run_id": run_id, "url": s["url"], "field": s["field"], "current": s.get("current"), "suggested": s["suggested"],
        "rationale": s.get("rationale"), "evidence_ref": s.get("evidence_ref"),
    }) for s in artifact.get("suggestions", [])]


def write_brief(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> UUID:
    kw = scope.fetchone("select id from keywords where project_id = %(project_id)s and keyword = %(kw)s", {"kw": artifact["keyword"]})
    return scope.insert("content_briefs", {
        "run_id": run_id, "keyword_id": kw["id"] if kw else None, "title": artifact["title"], "answer_block": artifact["answer_block"],
        "outline": jsonb(artifact.get("outline", [])), "draft": artifact["draft"], "sources": jsonb(artifact.get("sources", [])),
        "gate_result": artifact.get("_gate_result", "stage1_pass"), "status": "draft",
    })


def write_pitches(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> list[UUID]:
    return [scope.insert("pitches", {
        "run_id": run_id, "outlet_url": p["outlet_url"], "contact_hint": p.get("contact_hint"), "subject": p["subject"], "body": p["body"],
        "evidence_ref": p.get("evidence_ref"),
    }) for p in artifact.get("pitches", [])]


def _domain(url: str) -> str:
    return url.removeprefix("https://").removeprefix("http://").split("/", 1)[0]


def write_link_plan(scope: ProjectScope, run_id: UUID, artifact: dict[str, Any]) -> list[UUID]:
    """One link_plans row per run, and one link_opportunities row per (project, kind, domain) across runs. An item
    keeps its id and first run and takes the latest state; done_at is set when it turns done and cleared if it
    reopens. A prospect from an earlier run that now links to the project becomes 'won'. Returns the plan id first."""
    plan_id = scope.insert("link_plans", {
        "run_id": run_id, "phase": artifact.get("phase"), "signals": jsonb(artifact.get("signals", [])), "tactics": jsonb(artifact.get("tactics", [])),
        "velocity": jsonb(artifact["velocity"]) if artifact.get("velocity") else None, "anchors": jsonb(artifact.get("anchors", [])),
        "flags": jsonb(artifact.get("flags", [])), "competitors": jsonb(artifact.get("competitors", [])),
        "profile_copy": jsonb(artifact["profile_copy"]) if artifact.get("profile_copy") else None,
        "prospects_skipped": artifact.get("prospects_skipped", 0),
    })
    ids: list[UUID] = [plan_id]
    rows = [{"kind": c["kind"], "domain": _domain(c["url"]), "title": c["platform"], "category": None, "status": c["status"],
             "in_same_as": c["in_same_as"], "links_to_you": c["links_to_you"], "competitors": None, "competitor_domains": None,
             "rank": None, "spam_score": None, "action": c["action"], "evidence_ref": c.get("evidence_ref"),
             "linking_url": None, "linking_title": None} for c in artifact.get("checklist", [])]
    rows += [{"kind": "prospect", "domain": _domain(p["url"]), "title": _domain(p["url"]), "category": p.get("category"), "status": "todo",
              "in_same_as": None, "links_to_you": False, "competitors": p["competitors"], "competitor_domains": [_domain(u) for u in p["competitor_urls"]],
              "rank": p.get("domain_rank"), "spam_score": p.get("spam_score"), "action": p.get("approach"), "evidence_ref": p.get("evidence_ref"),
              "linking_url": p.get("linking_url"), "linking_title": p.get("evidence")}
             for p in artifact.get("prospects", [])]
    for r in rows:
        fp = hashlib.sha256(f"{r['kind']}|{r['domain']}".encode()).hexdigest()
        row = scope.fetchone(
            """insert into link_opportunities (project_id, run_id, last_run_id, fingerprint, kind, domain, title, category, status, in_same_as,
                                               links_to_you, competitors, competitor_domains, rank, spam_score, action, evidence_ref, linking_url, linking_title, done_at)
               values (%(project_id)s, %(run_id)s, %(run_id)s, %(fp)s, %(kind)s, %(domain)s, %(title)s, %(category)s, %(status)s, %(in_same_as)s,
                       %(links_to_you)s, %(competitors)s, %(competitor_domains)s, %(rank)s, %(spam_score)s, %(action)s, %(evidence_ref)s,
                       %(linking_url)s, %(linking_title)s, case when %(status)s = 'todo' then null else now() end)
               on conflict (project_id, fingerprint) do update set
                 last_run_id = excluded.last_run_id, last_seen_at = now(), title = excluded.title,
                 category = coalesce(excluded.category, link_opportunities.category), status = excluded.status,
                 in_same_as = excluded.in_same_as, links_to_you = excluded.links_to_you, competitors = excluded.competitors,
                 competitor_domains = excluded.competitor_domains, rank = excluded.rank, spam_score = excluded.spam_score,
                 action = coalesce(excluded.action, link_opportunities.action), evidence_ref = excluded.evidence_ref,
                 linking_url = coalesce(excluded.linking_url, link_opportunities.linking_url),
                 linking_title = coalesce(excluded.linking_title, link_opportunities.linking_title),
                 done_at = case when excluded.status = 'todo' then null else coalesce(link_opportunities.done_at, now()) end
               returning id""",
            {"run_id": run_id, "fp": fp, **r})
        ids.append(row["id"])
    linking = [r["domain"] for r in scope.fetchall(
        "select distinct domain from raw_referring_domains where project_id = %(project_id)s and run_id = %(run_id)s and role = 'own' and lost_date is null",
        {"run_id": run_id})]
    if linking:
        cur = scope.execute(
            """update link_opportunities set status = 'won', done_at = now(), links_to_you = true, last_seen_at = now()
                where project_id = %(project_id)s and kind = 'prospect' and status = 'todo' and last_run_id <> %(run_id)s
                  and exists (select 1 from unnest(%(linking)s::text[]) d where d = domain or d like '%%.' || domain)""",
            {"run_id": run_id, "linking": linking})
        if cur.rowcount:
            scope.audit("orchestrator", "link_prospects_won", {"count": cur.rowcount}, run_id)
    return ids


PERSISTERS = {"technical": write_issues, "ecommerce": write_issues, "trend": write_trend_events, "report": write_report,
              "keyword": write_keywords, "onpage": write_onpage, "content": write_brief, "offpage": write_pitches,
              "linkbuilding": write_link_plan}


def analyse_and_gate(rt: Runtime, project, run_id: UUID, agent: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one analyst, gate its output (stage 1, then stage 2 when it cites documents), persist when clean.

    Returns a JSON-safe summary for graph state."""
    artifact = rt.run_analyst(project, run_id, agent, params)
    s1 = rt.run_gate_stage1(project, run_id, agent, artifact)
    out: dict[str, Any] = {"blocked": s1.blocked, "violations": [v.as_dict() for v in s1.violations], "dropped": s1.dropped,
                           "artifact": None, "written": [], "stage2": None}
    if s1.blocked:
        return out
    cleaned = s1.artifact
    if cleaned.get("sources") is not None:
        s2 = rt.run_gate_stage2(project, run_id, agent, cleaned)
        out["stage2"] = {"verdict": s2.verdict, "verified": s2.claims_verified, "cut": s2.claims_cut, "findings": s2.findings}
        if s2.verdict == "blocked":
            out["blocked"] = True
            return out
        cleaned = s2.artifact
        cleaned["_gate_result"] = f"stage2_{s2.verdict}"
    out["artifact"] = cleaned
    persist = PERSISTERS.get(agent)
    if persist:
        with rt.scope(project.id) as s:
            written = persist(s, run_id, cleaned)
        out["written"] = [str(w) for w in written] if isinstance(written, list) else [str(written)]
    return out
