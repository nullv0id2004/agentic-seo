"""Cron schedule in IST. Projects are staggered so shared API quotas are not hit all at once.

Every firing is idempotent: the logical date is part of the run key, so a scheduler restart inside
the same minute cannot start a second run.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
STAGGER_MINUTES = 20


@dataclass(frozen=True)
class Schedule:
    workflow: str
    hour: int
    minute: int = 0
    weekday: int | None = None        # 0 = Monday
    day_of_month: int | None = None
    months: tuple[int, ...] | None = None
    logical: str = "date"             # 'date' | 'week' | 'month' | 'quarter'


SCHEDULES: tuple[Schedule, ...] = (
    Schedule("daily_collect", 5, 30),
    Schedule("daily_probe", 7, 0),
    Schedule("daily_digest", 9, 0),
    Schedule("weekly_monitor", 6, 0, weekday=0, logical="week"),
    Schedule("monthly_full", 6, 0, day_of_month=4, logical="month"),
    Schedule("quarterly_keyword", 6, 30, day_of_month=2, months=(1, 4, 7, 10), logical="quarter"),
)


def logical_date(s: Schedule, now: datetime) -> str:
    d = now.date()
    if s.logical == "week":
        return f"week:{d.isocalendar().year}-W{d.isocalendar().week:02d}"
    if s.logical == "month":
        prev = (d.replace(day=1) - timedelta(days=1))
        return f"month:{prev.strftime('%Y-%m')}"
    if s.logical == "quarter":
        return f"quarter:{d.year}-Q{(d.month - 1) // 3 + 1}"
    return d.isoformat()


def due(now_utc: datetime, projects: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str, str]]:
    """Return (project, workflow, logical_date) triples that fire in this minute."""
    now = now_utc.astimezone(IST)
    out = []
    for idx, project in enumerate(projects):
        for s in SCHEDULES:
            fire = now.replace(hour=s.hour, minute=s.minute, second=0, microsecond=0) + timedelta(minutes=STAGGER_MINUTES * idx)
            if s.weekday is not None and now.weekday() != s.weekday:
                continue
            if s.day_of_month is not None and now.day != s.day_of_month:
                continue
            if s.months is not None and now.month not in s.months:
                continue
            if now.hour == fire.hour and now.minute == fire.minute:
                out.append((project, s.workflow, logical_date(s, now)))
    return out


def tick(rt, now_utc: datetime | None = None) -> list[dict[str, Any]]:
    """One scheduler tick. Safe to call every minute from a loop or a cron."""
    from contracts.project import Project
    from db.connection import connect, list_active_projects
    from orchestrator.runner import run_workflow

    now_utc = now_utc or datetime.now(UTC)
    with connect(rt.db_url) as conn:
        projects = list_active_projects(conn)
    started = []
    for row, workflow, logical in due(now_utc, projects):
        project = Project.from_row(row)
        if workflow == "daily_digest":
            from orchestrator import notify
            with rt.scope(project.id) as s:
                d = notify.daily_digest(s)
            started.append({"project": project.slug, "workflow": workflow, "digest": d})
            continue
        if workflow == "quarterly_keyword" and "keyword" not in project.enabled_agents:
            continue
        try:
            h = run_workflow(rt, project, workflow, "cron", logical)
            started.append({"project": project.slug, "workflow": workflow, "run_id": str(h.id), "status": h.status})
        except Exception as e:  # the run row already records the failure; keep the loop alive
            started.append({"project": project.slug, "workflow": workflow, "error": f"{type(e).__name__}: {e}"})
    return started
