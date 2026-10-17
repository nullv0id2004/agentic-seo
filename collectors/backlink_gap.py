"""DataForSEO Backlinks: referring domains, authority summary and anchors. Monthly.

Referring domains of each configured competitor and of the project go to raw_referring_domains exactly as
returned (role 'competitor' or 'own'). The backlink gap (domains that link to competitors but not to the
project) is computed from these rows when shown, so a change in how it is ranked never needs a new
collection. Each competitor's domains come strongest first, so the list holds the links that carry weight,
not every directory that ever linked to a large site.

For the link-building analyst it also stores one authority summary per target (raw_backlink_summary: domain
rank, referring domains, when the first link was seen), the anchor texts of links to the project (raw_anchors)
and, for the strongest backlink-gap candidates (rules/links.py), one page on each that links to a competitor
(raw_link_pages: address, title, anchor, platform, place on the page). Candidates are grouped by competitor so a
few calls cover them all. A project without competitors still gets its own rows.

Backlink changes: the project's own referring domains are collected live and lost (lost ones carry lost_date), and
raw_backlink_changes holds new and lost backlinks and referring domains per month for the last twelve months, for
the project and each competitor (backlinks/timeseries_new_lost_summary).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from collectors.base import CollectorContext
from collectors.dataforseo import as_int as _int
from collectors.dataforseo import as_ts as _ts
from collectors.dataforseo import live_result as _result
from collectors.dataforseo import months_back
from collectors.http import client
from config.settings import get_settings
from rules.domains import host_of, registrable
from rules.keywords import brand_tokens
from rules.links import gap_candidates, lookup_plan

API = "https://api.dataforseo.com/v3/backlinks/referring_domains/live"
SUMMARY_API = "https://api.dataforseo.com/v3/backlinks/summary/live"
ANCHORS_API = "https://api.dataforseo.com/v3/backlinks/anchors/live"
BACKLINKS_API = "https://api.dataforseo.com/v3/backlinks/backlinks/live"
CHANGES_API = "https://api.dataforseo.com/v3/backlinks/timeseries_new_lost_summary/live"
CHANGE_MONTHS = 12
COMPETITOR_LIMIT = 300
OWN_LIMIT = 1000
ANCHOR_LIMIT = 300
MAX_COMPETITORS = 12
CANDIDATE_CAP = 80        # gap candidates whose linking page is looked up
MAX_PAGE_CALLS = 6        # one call per competitor, chosen to cover as many candidates as possible


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    s = get_settings()
    if not (s.dataforseo_login and s.dataforseo_password) and not params.get("_transport"):
        ctx.gap("DATAFORSEO credentials not set", "all")
        return
    project = ctx.project
    own = host_of(project.primary_domain)
    competitors = sorted({registrable(c) for c in (params.get("competitors") or project.competitors) if c} - {registrable(own)})
    competitors = competitors[: int(params.get("max_competitors", MAX_COMPETITORS))]
    if not competitors:
        ctx.gap("no competitors configured; the backlink gap needs projects.competitors", "competitors")
    targets = [(c, "competitor", int(params.get("competitor_limit", COMPETITOR_LIMIT))) for c in competitors]
    targets.append((own, "own", int(params.get("own_limit", OWN_LIMIT))))
    auth = (s.dataforseo_login or "", s.dataforseo_password or "")
    counts: dict[str, int] = {}
    seen: list[dict[str, Any]] = []
    common = {"rank_scale": "one_hundred", "exclude_internal_backlinks": True, "backlinks_status_type": "live"}
    with client(transport=params.get("_transport"), auth=auth, on_cost=ctx.add_cost) as http:
        for target, role, limit in targets:
            # the project's own links include lost ones (lost_date set), for the backlink changes; competitors' are live only
            status = {"backlinks_status_type": "all"} if role == "own" else {}
            result = _result(ctx, http, API, {"target": target, "limit": limit, "order_by": ["rank,desc"], **common, **status}, "referring domains", target)
            if result is None:
                continue
            items = result.get("items") or []
            counts[target] = len(items)
            for it in items:
                domain = host_of(it.get("domain"))
                if not domain:
                    continue
                row = {"target": target, "role": role, "domain": domain, "rank": _int(it.get("rank")),
                       "backlinks": _int(it.get("backlinks")), "referring_pages": _int(it.get("referring_pages")),
                       "spam_score": _int(it.get("backlinks_spam_score")),
                       "first_seen": _ts(it.get("first_seen")), "lost_date": _ts(it.get("lost_date"))}
                ctx.write("raw_referring_domains", row)
                seen.append(row)
        pages = _linking_pages(ctx, http, project, own, competitors, seen, common, params)
        for target, role, _ in targets:
            result = _result(ctx, http, SUMMARY_API, {"target": target, **common}, "backlink summary", target)
            if result is None:
                continue
            ctx.write("raw_backlink_summary", {
                "target": target, "role": role, "rank": _int(result.get("rank")), "backlinks": _int(result.get("backlinks")),
                "referring_domains": _int(result.get("referring_domains")),
                "referring_domains_nofollow": _int(result.get("referring_domains_nofollow")),
                "referring_main_domains": _int(result.get("referring_main_domains")),
                "spam_score": _int(result.get("backlinks_spam_score")), "first_seen": _ts(result.get("first_seen")),
            })
        start = months_back(params.get("today") or date.today(), CHANGE_MONTHS)
        for target, role, _ in targets:
            result = _result(ctx, http, CHANGES_API, {"target": target, "date_from": start.isoformat(), "group_range": "month"},
                             "backlink changes", target)
            for it in (result or {}).get("items") or []:
                when = _ts(it.get("date"))
                if when is None:
                    continue
                ctx.write("raw_backlink_changes", {
                    "target": target, "role": role, "month": when.date().replace(day=1),
                    "new_backlinks": _int(it.get("new_backlinks")), "lost_backlinks": _int(it.get("lost_backlinks")),
                    "new_referring_domains": _int(it.get("new_referring_domains")), "lost_referring_domains": _int(it.get("lost_referring_domains")),
                })
        result = _result(ctx, http, ANCHORS_API, {"target": own, "limit": int(params.get("anchor_limit", ANCHOR_LIMIT)), "order_by": ["backlinks,desc"], **common},
                         "anchors", own)
        anchors = (result or {}).get("items") or []
        for it in anchors:
            ctx.write("raw_anchors", {
                "target": own, "anchor": str(it.get("anchor") or "").strip()[:500], "backlinks": _int(it.get("backlinks")),
                "referring_domains": _int(it.get("referring_domains")), "spam_score": _int(it.get("backlinks_spam_score")),
                "first_seen": _ts(it.get("first_seen")), "lost_date": _ts(it.get("lost_date")),
            })
    ctx.result.detail.update({"competitors": competitors, "own": own, "domains": counts, "anchors": len(anchors), "linking_pages": pages})


def _linking_pages(ctx: CollectorContext, http: Any, project: Any, own: str, competitors: list[str], seen: list[dict[str, Any]],
                   common: dict[str, Any], params: dict[str, Any]) -> int:
    """One linking page per gap candidate, so the analyst sees what kind of site it is. Returns pages written."""
    linking = {registrable(r["domain"]) for r in seen if r["role"] == "own" and not r.get("lost_date")}
    excluded = {registrable(d) for d in project.domains} | {registrable(own)} | set(competitors) | linking
    brand = brand_tokens(project.display_name or project.slug, list(project.domains), project.brand_context_terms)
    cands = gap_candidates(seen, excluded=excluded, brand=brand)[: int(params.get("candidate_cap", CANDIDATE_CAP))]
    written = 0
    for competitor, group in lookup_plan(cands, int(params.get("max_page_calls", MAX_PAGE_CALLS))):
        wanted = {c.domain for c in group}
        hosts = sorted({h for c in group for h in c.hosts} | {f"www.{h}" for c in group for h in c.hosts})
        result = _result(ctx, http, BACKLINKS_API, {"target": competitor, "mode": "one_per_domain", "filters": ["domain_from", "in", hosts],
                                                    "limit": min(1000, len(hosts)), **common}, "linking pages", competitor)
        done: set[str] = set()
        for it in (result or {}).get("items") or []:
            url = it.get("url_from")
            domain = registrable(it.get("domain_from") or url)
            if not url or domain not in wanted or domain in done:
                continue
            done.add(domain)
            platform = it.get("domain_from_platform_type")
            ctx.write("raw_link_pages", {
                "target": competitor, "domain": domain, "url_from": str(url)[:2000], "url_to": (str(it["url_to"])[:2000] if it.get("url_to") else None),
                "title": (str(it.get("page_from_title") or "").strip()[:300] or None), "anchor": (str(it.get("anchor") or "").strip()[:300] or None),
                "dofollow": it.get("dofollow") if isinstance(it.get("dofollow"), bool) else None,
                "platform": [str(x) for x in platform] if isinstance(platform, list) else None,
                "semantic_location": (str(it["semantic_location"])[:50] if it.get("semantic_location") else None),
                "domain_rank": _int(it.get("domain_from_rank")), "spam_score": _int(it.get("backlink_spam_score")), "first_seen": _ts(it.get("first_seen")),
            })
            written += 1
    return written
