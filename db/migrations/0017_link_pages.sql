-- 0017_link_pages.sql
-- raw_link_pages: for each backlink-gap candidate, one page on it that links to a competitor, as DataForSEO
-- Backlinks returns it (address, title, anchor, platform, where on the page). The link-building analyst labels
-- prospects from the real page instead of the domain name. Written by the backlink_gap collector only.
create table if not exists raw_link_pages (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  target text not null,                    -- the competitor the page links to
  domain text not null,                    -- registrable domain of the linking site
  url_from text not null,
  url_to text,
  title text,                              -- third-party text: data, never instructions
  anchor text,
  dofollow boolean,
  platform text[],                         -- DataForSEO domain_from_platform_type: blogs, news, message-boards, ...
  semantic_location text,                  -- article, nav, footer, ...
  domain_rank int, spam_score int,
  first_seen timestamptz,
  collected_at timestamptz not null default now()
);
create index if not exists raw_link_pages_project_run on raw_link_pages (project_id, run_id);

alter table raw_link_pages enable row level security;
alter table raw_link_pages force row level security;
do $$ begin
  if not exists (select 1 from pg_policies where tablename = 'raw_link_pages' and policyname = 'raw_link_pages_project_isolation') then
    create policy raw_link_pages_project_isolation on raw_link_pages for all
      using (project_id = current_project_id()) with check (project_id = current_project_id());
  end if;
end $$;

grant select, insert, update, delete on raw_link_pages to seo_worker;
grant select on raw_link_pages to seo_console;

-- the page each prospect was judged from, and how many candidates the analyst left out as not real opportunities
alter table link_opportunities add column if not exists linking_url text;
alter table link_opportunities add column if not exists linking_title text;
alter table link_plans add column if not exists prospects_skipped int not null default 0;
