"""DataForSEO backlinks: referring pages -> mentions (kind='link'). Monthly. Upserts on (source_url, kind)."""
from __future__ import annotations

from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext
from collectors.http import client
from config.settings import get_settings

API = "https://api.dataforseo.com/v3/backlinks/backlinks/live"


def collect(ctx: CollectorContext, params: dict[str, Any]) -> None:
    s = get_settings()
    if not (s.dataforseo_login and s.dataforseo_password) and not params.get("_transport"):
        ctx.gap("DATAFORSEO credentials not set", "all")
        return
    auth = (s.dataforseo_login or "", s.dataforseo_password or "")
    target = ctx.project.primary_domain
    with client(transport=params.get("_transport"), auth=auth) as http:
        ratelimit.acquire("dataforseo")
        r = http.post(API, json=[{"target": target, "mode": "one_per_domain", "limit": int(params.get("limit", 1000)), "filters": ["dofollow", "=", True]}])
        if r.status_code != 200:
            ctx.gap(f"backlinks returned {r.status_code}", target)
            return
        try:
            task = r.json()["tasks"][0]
            if task.get("status_code") != 20000:
                ctx.gap(f"backlinks task {task.get('status_code')}: {task.get('status_message')}", target)
                return
            items = (task.get("result") or [{}])[0].get("items") or []
        except (KeyError, IndexError, TypeError, ValueError):
            ctx.gap("backlinks response shape unexpected", target)
            return
    for it in items:
        src = it.get("url_from")
        if not src:
            continue
        ctx.scope.execute(
            """insert into mentions (project_id, source_url, target_url, kind, domain_rating, first_seen)
               values (%(project_id)s, %(src)s, %(dst)s, 'link', %(dr)s, %(seen)s)
               on conflict (project_id, source_url, kind) do update set target_url = excluded.target_url, domain_rating = excluded.domain_rating""",
            {"src": src, "dst": it.get("url_to"), "dr": it.get("domain_from_rank"), "seen": (it.get("first_seen") or "")[:10] or None},
        )
        ctx.result.rows_written += 1
