"""One DataForSEO live task: POST, check both status levels, return the first result object or record a gap."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from collectors import ratelimit
from collectors.base import CollectorContext


def live_result(ctx: CollectorContext, http: Any, url: str, body: dict[str, Any], what: str, scope: str) -> dict[str, Any] | None:
    ratelimit.acquire("dataforseo")
    r = http.post(url, json=[body])
    if r.status_code != 200:
        ctx.gap(f"{what} returned {r.status_code}", scope)
        return None
    try:
        task = r.json()["tasks"][0]
    except (KeyError, IndexError, TypeError, ValueError):
        ctx.gap(f"{what} response shape unexpected", scope)
        return None
    if task.get("status_code") != 20000:
        ctx.gap(f"{what} task status {task.get('status_code')}: {task.get('status_message')}", scope)
        return None
    return (task.get("result") or [None])[0] or {}


def as_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def as_num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def as_ts(v: Any) -> datetime | None:
    """DataForSEO writes '2019-11-15 12:57:46 +00:00'; anything unparseable is stored as null, not guessed."""
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return datetime.fromisoformat(v.strip().replace(" +", "+").replace(" -", "-"))
    except ValueError:
        return None


def months_back(today: date, n: int) -> date:
    """First day of the month n months before today's month."""
    y, m = today.year, today.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, 1)
