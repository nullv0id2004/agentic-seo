-- 0012_index_coverage_keyword_ideas.sql
-- 1. raw_url_inspection: Google's full URL Inspection verdict per URL. raw_crawl_pages.indexable said only
--    yes or no; the coverage state ("Crawled - currently not indexed", "URL is unknown to Google", ...)
--    is what decides the fix, so it is kept. Written by the gsc_inspection collector only.
-- 2. raw_keyword_ideas: keyword discovery from DataForSEO Labs (ideas from seeds, and keywords that
--    competitors rank for). Written by the keyword_discovery collector only.
-- 3. approvals.approver_input: what the approver supplies when deciding (a pitch recipient's address).
--    The console may write this column and nothing else new; the payload the agent proposed is unchanged.
create table if not exists raw_url_inspection (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, url text not null,
  verdict text, coverage_state text, indexing_state text, robots_txt_state text, page_fetch_state text,
  last_crawl_time timestamptz, crawled_as text, google_canonical text, user_canonical text,
  sitemaps jsonb, referring_urls jsonb,
  collected_at timestamptz not null default now()
);
create index if not exists raw_url_inspection_project_run on raw_url_inspection (project_id, run_id);
create index if not exists raw_url_inspection_project_url on raw_url_inspection (project_id, url, collected_at desc);

create table if not exists raw_keyword_ideas (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, keyword text not null,
  source text not null,                 -- 'idea' (from seeds) | 'competitor' (a competitor ranks for it)
  seed text, competitor text, competitor_rank int, competitor_url text,
  search_volume int, keyword_difficulty int, cpc numeric, competition numeric, intent text,
  location_code int, language_code text,
  collected_at timestamptz not null default now()
);
create index if not exists raw_keyword_ideas_project_run on raw_keyword_ideas (project_id, run_id);

do $$
declare t text;
begin
  for t in select unnest(array['raw_url_inspection','raw_keyword_ideas'])
  loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    if not exists (select 1 from pg_policies where tablename = t and policyname = t || '_project_isolation') then
      execute format(
        'create policy %I on %I for all using (project_id = current_project_id()) with check (project_id = current_project_id())',
        t || '_project_isolation', t);
    end if;
  end loop;
end $$;

grant select, insert, update, delete on raw_url_inspection, raw_keyword_ideas to seo_worker;
grant select on raw_url_inspection, raw_keyword_ideas to seo_console;

alter table approvals add column if not exists approver_input jsonb;
grant update (approver_input) on approvals to seo_console;
