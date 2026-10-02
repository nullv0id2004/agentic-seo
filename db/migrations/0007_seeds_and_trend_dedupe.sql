-- 0007_seeds_and_trend_dedupe.sql
-- 1. projects.keyword_seeds: the YAML field existed, the column did not, so Project.from_row always
--    saw [] in production and the quarterly keyword run had nothing to price.
alter table projects add column if not exists keyword_seeds text[] not null default '{}';

-- 2. trend_events: one row per (project, kind, source_url) across runs. Every weekly run used to
--    insert the same Google updates again (32 rows for 8 updates after four runs).
alter table trend_events
  add column if not exists last_run_id uuid,
  add column if not exists last_seen_at timestamptz not null default now(),
  add column if not exists seen_count int not null default 1;
update trend_events set last_run_id = coalesce(last_run_id, run_id), last_seen_at = created_at where last_run_id is null;
delete from trend_events older
 using trend_events newer
 where older.project_id = newer.project_id and older.kind = newer.kind and older.source_url = newer.source_url
   and (older.created_at, older.id) < (newer.created_at, newer.id);
create unique index if not exists trend_events_project_kind_source on trend_events (project_id, kind, source_url);
