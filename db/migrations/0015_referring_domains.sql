-- 0015_referring_domains.sql
-- raw_referring_domains: the domains linking to each configured competitor and to the project itself, as
-- DataForSEO Backlinks returns them. The console's backlink gap is computed from these rows: domains that link
-- to competitors but not to the project. Written by the backlink_gap collector only.
create table if not exists raw_referring_domains (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  target text not null,                -- the domain the links point to
  role text not null,                  -- 'competitor' | 'own'
  domain text not null,                -- the referring domain
  rank int,                            -- DataForSEO domain rank, 0-100
  backlinks int, referring_pages int, spam_score int,
  first_seen timestamptz, lost_date timestamptz,
  collected_at timestamptz not null default now()
);
create index if not exists raw_referring_domains_project_run on raw_referring_domains (project_id, run_id);

alter table raw_referring_domains enable row level security;
alter table raw_referring_domains force row level security;
do $$ begin
  if not exists (select 1 from pg_policies where tablename = 'raw_referring_domains' and policyname = 'raw_referring_domains_project_isolation') then
    create policy raw_referring_domains_project_isolation on raw_referring_domains for all
      using (project_id = current_project_id()) with check (project_id = current_project_id());
  end if;
end $$;

grant select, insert, update, delete on raw_referring_domains to seo_worker;
grant select on raw_referring_domains to seo_console;
