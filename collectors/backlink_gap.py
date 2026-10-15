"""DataForSEO Backlinks: referring domains of each configured competitor and of the project. Monthly.

Written to raw_referring_domains exactly as returned (role 'competitor' or 'own'). The backlink gap (domains
that link to competitors but not to the project) is computed from these rows when shown, so a change in how
it is ranked never needs a new collection. Each competitor's domains come strongest first, so the list holds
the links that carry weight, not every directory that ever linked to a large site.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client
from config.settings import get_settings
from rules.domains import host_of, registrable

API = "https://api.dataforseo.com/v3/backlinks/referring_domains/live"
COMPETITOR_LIMIT = 300
OWN_LIMIT = 1000
MAX_COMPETITORS = 12


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _ts(v: Any) -> datetime | None:
    """DataForSEO writes '2019-11-15 12:57:46 +00:00'; anything unparseable is stored as null, not guessed."""
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return datetime.fromisoformat(v.strip().replace(" +", "+").replace(" -", "-"))
    except ValueError:
        return None


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
    with client(transport=params.get("_transport"), auth=auth, on_cost=ctx.add_cost) as http:
        for target, role, limit in targets:
            ratelimit.acquire("dataforseo")
            r = http.post(API, json=[{"target": target, "limit": limit, "order_by": ["rank,desc"], "rank_scale": "one_hundred",
                                      "exclude_internal_backlinks": True, "backlinks_status_type": "live"}])
            if r.status_code != 200:
                ctx.gap(f"referring domains returned {r.status_code}", target)
                continue
            try:
                task = r.json()["tasks"][0]
            except (KeyError, IndexError, TypeError, ValueError):
                ctx.gap("referring domains response shape unexpected", target)
                continue
            if task.get("status_code") != 20000:
                ctx.gap(f"referring domains task status {task.get('status_code')}: {task.get('status_message')}", target)
                continue
            items = ((task.get("result") or [None])[0] or {}).get("items") or []
            counts[target] = len(items)
            for it in items:
                domain = host_of(it.get("domain"))
                if not domain:
                    continue
                ctx.write("raw_referring_domains", {
                    "target": target, "role": role, "domain": domain, "rank": _int(it.get("rank")),
                    "backlinks": _int(it.get("backlinks")), "referring_pages": _int(it.get("referring_pages")),
                    "spam_score": _int(it.get("backlinks_spam_score")),
                    "first_seen": _ts(it.get("first_seen")), "lost_date": _ts(it.get("lost_date")),
                })
    ctx.result.detail.update({"competitors": competitors, "own": own, "domains": counts})
