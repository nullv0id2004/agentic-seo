-- 0001_init.sql
-- Multi-project agentic SEO system. Single migration. Every table carries project_id.
-- There is no property enum anywhere. Adding a project is a row insert.

create extension if not exists pgcrypto;

-- ============ TENANCY AND CONFIG ============

create table projects (
  id              uuid primary key default gen_random_uuid(),
  slug            text unique not null,
  display_name    text not null,
  domains         text[] not null,
  vertical        text not null,          -- 'recruitment' | 'ecommerce' | 'content' | 'saas'
  gsc_property    text,
  ga4_property_id text,
  credentials_ref text,                   -- Key Vault secret name, never a raw secret
  approver_id     uuid not null,
  monthly_cost_cap_usd numeric not null default 50,
  enabled_agents  text[] not null default '{}',
  allowed_schema_types text[] not null default '{}',
  monthly_content_cap int not null default 4,   -- Section 13.9: deliberately low, not a placeholder
  critical_paths  text[] not null default '{}',   -- probed by header_probe after every deploy and daily
  active          boolean not null default true,
  created_at      timestamptz not null default now(),
  constraint projects_vertical_check check (vertical in ('recruitment','ecommerce','content','saas')),
  constraint projects_slug_not_cruise_guru check (slug <> 'cruise-guru' and slug <> 'cruiseguru' and slug <> 'cruise_guru')
);

create table brand_rules (
  id          uuid primary key default gen_random_uuid(),
  project_id  uuid not null references projects(id) on delete cascade,
  rule_type   text not null,              -- 'banned_phrase' | 'required_phrase' | 'char_ban' | 'regex'
  pattern     text not null,
  severity    text not null,              -- 'block' | 'warn'
  rationale   text,
  active      boolean not null default true,
  constraint brand_rules_type_check check (rule_type in ('banned_phrase','required_phrase','char_ban','regex')),
  constraint brand_rules_severity_check check (severity in ('block','warn'))
);
create index on brand_rules (project_id);

create table critical_rules (
  id          uuid primary key default gen_random_uuid(),
  project_id  uuid not null references projects(id) on delete cascade,
  rule_key    text not null,              -- 'no_indexable_auth_route', 'no_indexable_cart', ...
  url_pattern text not null,              -- regex over path
  assertion   text not null,              -- 'must_noindex' | 'must_index' | 'must_not_appear_in_sitemap' | ...
  active      boolean not null default true,
  unique (project_id, rule_key)
);

-- ============ RAW COLLECTION (deterministic writes only) ============

create table raw_gsc_performance (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  date date not null, query text, page text, country text, device text,
  clicks int, impressions int, ctr numeric, position numeric,
  source text not null default 'gsc',     -- 'gsc' | 'ga4' (ga4 is the sibling dataset)
  collected_at timestamptz not null default now()
);
create index on raw_gsc_performance (project_id, date);

create table raw_ga4_daily (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  date date not null, page_path text, channel text,
  sessions int, engaged_sessions int, conversions numeric,
  collected_at timestamptz not null default now()
);
create index on raw_ga4_daily (project_id, date);

create table raw_crawl_pages (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null,
  url text not null, status_code int, title text, meta_description text,
  h1 text[], canonical text, robots_meta text, x_robots_tag text,
  word_count int, in_sitemap boolean, schema_types text[],
  raw_jsonld jsonb, internal_links_out int, internal_links text[],
  indexable boolean,                      -- from gsc_inspection, null until inspected
  visible_price text,                     -- ecommerce: price as rendered on page, null elsewhere
  collected_at timestamptz not null default now()
);
create index on raw_crawl_pages (project_id, run_id, url);

create table raw_sitemap_urls (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, sitemap_url text not null, url text not null,
  collected_at timestamptz not null default now()
);
create index on raw_sitemap_urls (project_id, run_id);

create table raw_vitals (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, url text not null, strategy text,
  lcp_ms numeric, inp_ms numeric, cls numeric, source text,  -- 'lab' | 'field'
  collected_at timestamptz not null default now()
);
create index on raw_vitals (project_id, run_id);

create table raw_serp (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, query text not null, engine text, location text,
  results jsonb, ai_overview_present boolean, ai_overview_citations jsonb,
  collected_at timestamptz not null default now()
);
create index on raw_serp (project_id, run_id);

create table raw_keyword_metrics (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, keyword text not null,
  volume int, volume_is_range boolean not null default false,
  volume_low int, volume_high int, difficulty int, cpc numeric, source text,
  collected_at timestamptz not null default now()
);
create index on raw_keyword_metrics (project_id, keyword);

create table raw_fetched_documents (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, url text not null, http_status int,
  content_text text, content_hash text, fetched_at timestamptz not null default now(),
  is_untrusted boolean not null default true,   -- ALWAYS true. Read Section 13.
  constraint raw_fetched_documents_always_untrusted check (is_untrusted = true)
);
create index on raw_fetched_documents (project_id, run_id);

create table raw_search_status (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, update_name text not null, status text,
  started_at timestamptz, ended_at timestamptz, source_url text not null,
  collected_at timestamptz not null default now()
);

create table collection_gaps (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, collector text not null, reason text not null,
  affected_scope text, created_at timestamptz not null default now()
);
create index on collection_gaps (project_id, run_id);

-- ============ DERIVED / WORKING ============

create table keywords (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  keyword text not null, intent text, mapped_url text,
  cluster text, blocked_for_index boolean not null default false,
  latest_metric_id uuid references raw_keyword_metrics(id),
  updated_at timestamptz not null default now(),
  unique (project_id, keyword)
);

create table pages (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  url text not null, title text, meta text, h1 text,
  primary_keyword_id uuid references keywords(id),
  canonical text, indexable boolean, last_audited timestamptz,
  unique (project_id, url)
);

create table issues (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, url text, issue_type text not null,
  severity text not null,                 -- 'critical' | 'high' | 'medium' | 'low'
  evidence text not null,                 -- must cite a raw_* row id
  evidence_ref uuid,
  recommended_fix text, claude_code_prompt text,
  status text not null default 'open', verified boolean not null default false,
  created_at timestamptz not null default now(),
  constraint issues_severity_check check (severity in ('critical','high','medium','low'))
);
create index on issues (project_id, run_id);

create table content_briefs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid,
  keyword_id uuid references keywords(id),
  title text, answer_block text, outline jsonb, draft text,
  sources jsonb,                          -- [{claim, url, fetched_doc_id, verdict}]
  gate_result text, status text not null default 'draft',
  approved_by uuid, published_url text,
  created_at timestamptz not null default now()
);
create index on content_briefs (project_id, created_at);

create table mentions (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  source_url text not null, target_url text, kind text,  -- 'link' | 'mention' | 'ai_citation'
  domain_rating int, first_seen date,
  unique (project_id, source_url, kind)
);

create table pitches (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, outlet_url text not null, contact_hint text,
  subject text not null, body text not null, evidence_ref uuid,
  status text not null default 'draft',
  created_at timestamptz not null default now()
);

create table onpage_suggestions (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, url text not null, field text not null,
  current text, suggested text not null, rationale text, evidence_ref uuid,
  status text not null default 'proposed',
  created_at timestamptz not null default now()
);
create index on onpage_suggestions (project_id, run_id);

create table trend_events (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, kind text not null, name text not null, source_url text not null,
  observed_on date not null, detail text, evidence_ref uuid,
  created_at timestamptz not null default now()
);
create index on trend_events (project_id, observed_on desc);

create table reports (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid,
  period text not null, metrics jsonb, commentary text, caveats jsonb,
  created_at timestamptz not null default now()
);

-- ============ CONTROL ============

create table runs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  workflow text not null, trigger text not null,
  idempotency_key text not null,
  status text not null,                   -- 'running'|'paused_for_approval'|'done'|'failed'|'halted_budget'
  started_at timestamptz not null default now(), ended_at timestamptz,
  cost_usd numeric not null default 0, tokens_in bigint default 0, tokens_out bigint default 0,
  logical_date date,
  halt_reason text,
  unique (project_id, workflow, idempotency_key),
  constraint runs_status_check check (status in ('running','paused_for_approval','done','failed','halted_budget','skipped'))
);
create index on runs (project_id, workflow, started_at desc);

create table gate_results (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, source_agent text not null, artifact_ref uuid,
  stage1_violations jsonb,                -- deterministic
  stage2_verdict text,                    -- 'pass'|'corrected'|'blocked'
  claims_verified int, claims_cut int, detail jsonb,
  created_at timestamptz not null default now()
);
create index on gate_results (project_id, run_id);

create table approvals (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, action_type text not null, payload jsonb not null,
  summary_plain_english text not null, requested_by_agent text not null,
  status text not null default 'pending', approver_id uuid,
  decided_at timestamptz, reversal_payload jsonb,
  severity text not null default 'normal',           -- 'normal' | 'critical' (critical breaks through the digest)
  notified_at timestamptz,
  executed_at timestamptz, execution_result jsonb, reversed_at timestamptz,
  created_at timestamptz not null default now(),
  constraint approvals_status_check check (status in ('pending','approved','rejected','executed','reversed','expired')),
  -- Section 8: an action that cannot describe its own reversal cannot be queued.
  constraint approvals_reversal_required check (reversal_payload is not null and reversal_payload <> 'null'::jsonb)
);
create index on approvals (project_id, status, created_at);

create table agent_logs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, agent text not null, node text,
  inputs_digest text, output jsonb, status text, retries int default 0,
  model text, tokens_in int, tokens_out int, cost_usd numeric,
  created_at timestamptz not null default now()
);
create index on agent_logs (project_id, run_id);

create table audit_log (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid, actor text not null, event text not null, detail jsonb,
  created_at timestamptz not null default now()
);
create index on audit_log (project_id, created_at);

-- ============ ROW LEVEL SECURITY ============
-- Every table is keyed on project_id. The current project is carried in the transaction-local
-- setting app.project_id. A connection that has not set it sees zero rows, on every table.
-- The worker additionally puts an explicit project_id filter in every query; RLS is the
-- backstop, not the only line.

create or replace function current_project_id() returns uuid
language sql stable as $$
  select nullif(current_setting('app.project_id', true), '')::uuid
$$;

do $$
declare t text;
begin
  for t in
    select tablename from pg_tables
    where schemaname = 'public'
      and tablename <> 'projects'
      and tablename in (
        'brand_rules','critical_rules','raw_gsc_performance','raw_ga4_daily','raw_crawl_pages','raw_sitemap_urls',
        'raw_vitals','raw_serp','raw_keyword_metrics','raw_fetched_documents','raw_search_status',
        'collection_gaps','keywords','pages','issues','content_briefs','mentions','pitches','onpage_suggestions','trend_events','reports',
        'runs','gate_results','approvals','agent_logs','audit_log')
  loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format(
      'create policy %I on %I for all using (project_id = current_project_id()) with check (project_id = current_project_id())',
      t || '_project_isolation', t);
  end loop;
end $$;

alter table projects enable row level security;
alter table projects force row level security;
create policy projects_project_isolation on projects
  for all using (id = current_project_id()) with check (id = current_project_id());

-- The only table the worker may read without a project scope is projects, and only through a
-- security-definer function that exposes nothing but the active project list for scheduling.
create or replace function list_active_projects() returns setof projects
language sql security definer set search_path = public stable as $$
  select * from projects where active = true order by slug
$$;
