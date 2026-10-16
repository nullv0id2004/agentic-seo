-- 0016_link_building.sql
-- The link-building analyst: authority and anchor text as DataForSEO returns them (written by the
-- backlink_gap collector only), the profiles and directories each project should be listed on (rows seeded
-- from config/projects/*.yaml, like brand_rules), and the plan the analyst produces each run.

-- One row per target per run: the project's own domain and each configured competitor.
create table if not exists raw_backlink_summary (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  target text not null,
  role text not null,                      -- 'own' | 'competitor'
  rank int,                                -- DataForSEO domain rank, 0-100
  backlinks bigint, referring_domains int, referring_domains_nofollow int, referring_main_domains int,
  spam_score int,                          -- backlinks_spam_score, 0-100
  first_seen timestamptz,                  -- when DataForSEO first found a link to the target
  collected_at timestamptz not null default now()
);
create index if not exists raw_backlink_summary_project_run on raw_backlink_summary (project_id, run_id);

-- Anchor texts of links to the project's own domain.
create table if not exists raw_anchors (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  target text not null,
  anchor text not null,                    -- '' for links without text (images)
  backlinks int, referring_domains int, spam_score int,
  first_seen timestamptz, lost_date timestamptz,
  collected_at timestamptz not null default now()
);
create index if not exists raw_anchors_project_run on raw_anchors (project_id, run_id);

-- Where the project should have a presence. Seeded by the operator from config; the analyst checks each
-- one against the site's Organization sameAs and the domains that link to it.
create table if not exists link_targets (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  platform text not null,
  domain text not null,
  kind text not null check (kind in ('profile', 'directory', 'review')),
  priority int not null default 100,       -- lower first
  note text,
  active boolean not null default true,
  unique (project_id, domain)
);

-- One plan per run: phase, the signals behind it, recommended tactics, velocity, anchors, profile copy.
create table if not exists link_plans (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  phase text,                              -- 'foundation' | 'growth' | 'authority'; null when nothing was measured
  signals jsonb not null default '[]',
  tactics jsonb not null default '[]',
  velocity jsonb,
  anchors jsonb not null default '[]',
  flags jsonb not null default '[]',
  competitors jsonb not null default '[]',
  profile_copy jsonb,
  created_at timestamptz not null default now()
);
create index if not exists link_plans_project_created on link_plans (project_id, created_at desc);

-- The checklist (profiles, directories) and the prospects from the backlink gap, one row per domain across
-- runs. A prospect that starts linking to the project becomes 'won'.
create table if not exists link_opportunities (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  last_run_id uuid not null,
  fingerprint text not null,
  kind text not null check (kind in ('profile', 'directory', 'review', 'prospect')),
  domain text not null,
  title text not null,
  category text,
  status text not null default 'todo' check (status in ('todo', 'done', 'won')),
  in_same_as boolean, links_to_you boolean,
  competitors int, competitor_domains text[], rank int, spam_score int,
  action text,
  evidence_ref uuid,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  done_at timestamptz,
  unique (project_id, fingerprint)
);

do $$
declare t text;
begin
  for t in select unnest(array['raw_backlink_summary', 'raw_anchors', 'link_targets', 'link_plans', 'link_opportunities'])
  loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    if not exists (select 1 from pg_policies where tablename = t and policyname = t || '_project_isolation') then
      execute format('create policy %I on %I for all using (project_id = current_project_id()) with check (project_id = current_project_id())',
                     t || '_project_isolation', t);
    end if;
  end loop;
end $$;

grant select, insert, update, delete on raw_backlink_summary, raw_anchors, link_plans, link_opportunities to seo_worker;
grant select on link_targets to seo_worker;
grant select on raw_backlink_summary, raw_anchors, link_targets, link_plans, link_opportunities to seo_console;
