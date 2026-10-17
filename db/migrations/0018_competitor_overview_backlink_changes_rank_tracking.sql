-- 0018_competitor_overview_backlink_changes_rank_tracking.sql
-- Competitor overview: DataForSEO Labs domain metrics for the project and each competitor (current, monthly history,
-- top pages). Backlink changes: new and lost backlinks and referring domains per month. Daily rank tracking: how many
-- tracked keywords a project checks every day (0 = off). The raw tables are written by collectors only.

create table if not exists raw_domain_overview (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  target text not null,
  role text not null,                       -- 'own' | 'competitor'
  etv numeric,                              -- estimated monthly organic traffic
  keywords int,                             -- organic keywords ranking
  pos_1 int, pos_2_3 int, pos_4_10 int, pos_11_20 int, pos_21_100 int,
  is_new int, is_up int, is_down int, is_lost int,
  paid_etv numeric,
  location_code int,
  collected_at timestamptz not null default now()
);
create index if not exists raw_domain_overview_project_run on raw_domain_overview (project_id, run_id);

create table if not exists raw_domain_history (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  target text not null,
  role text not null,
  month date not null,                      -- first day of the month
  etv numeric, keywords int, pos_1 int, pos_2_3 int, pos_4_10 int,
  collected_at timestamptz not null default now()
);
create index if not exists raw_domain_history_project_run on raw_domain_history (project_id, run_id);

create table if not exists raw_domain_pages (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  target text not null,
  role text not null,
  page text not null,
  etv numeric, keywords int, pos_1 int, pos_2_3 int, pos_4_10 int,
  collected_at timestamptz not null default now()
);
create index if not exists raw_domain_pages_project_run on raw_domain_pages (project_id, run_id);

create table if not exists raw_backlink_changes (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  target text not null,
  role text not null,
  month date not null,
  new_backlinks int, lost_backlinks int, new_referring_domains int, lost_referring_domains int,
  collected_at timestamptz not null default now()
);
create index if not exists raw_backlink_changes_project_run on raw_backlink_changes (project_id, run_id);

do $$
declare t text;
begin
  for t in select unnest(array['raw_domain_overview', 'raw_domain_history', 'raw_domain_pages', 'raw_backlink_changes'])
  loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    if not exists (select 1 from pg_policies where tablename = t and policyname = t || '_project_isolation') then
      execute format('create policy %I on %I for all using (project_id = current_project_id()) with check (project_id = current_project_id())',
                     t || '_project_isolation', t);
    end if;
  end loop;
end $$;

grant select, insert, update, delete on raw_domain_overview, raw_domain_history, raw_domain_pages, raw_backlink_changes to seo_worker;
grant select on raw_domain_overview, raw_domain_history, raw_domain_pages, raw_backlink_changes to seo_console;

-- Daily rank tracking: the number of tracked keywords checked every day; 0 leaves it to the weekly check.
alter table projects add column if not exists rank_tracking_limit int not null default 0;
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'projects_rank_tracking_limit_range') then
    alter table projects add constraint projects_rank_tracking_limit_range check (rank_tracking_limit between 0 and 50);
  end if;
end $$;
