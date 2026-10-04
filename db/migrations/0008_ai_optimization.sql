-- 0008_ai_optimization.sql
-- Raw tables for DataForSEO's AI Optimization APIs. Only collectors write them (Section 13.4);
-- analysts read them through the runtime and every number they emit cites a row id.
create table if not exists raw_ai_keyword_metrics (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, keyword text not null, location_code int, language_code text,
  ai_search_volume int, monthly jsonb,
  collected_at timestamptz not null default now()
);
create index if not exists raw_ai_keyword_metrics_project_run on raw_ai_keyword_metrics (project_id, run_id);

create table if not exists raw_llm_mention_metrics (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, target text not null, target_kind text not null,   -- 'domain' | 'brand'
  platform text not null, location_code int, language_code text,
  mentions int, ai_search_volume int,
  collected_at timestamptz not null default now()
);
create index if not exists raw_llm_mention_metrics_project_run on raw_llm_mention_metrics (project_id, run_id);

create table if not exists raw_llm_mentions (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, platform text not null, model_name text, location_code int, language_code text,
  question text not null, answer text, sources jsonb, cites_project boolean not null default false,
  ai_search_volume int, is_web_search_based boolean, brand_entities jsonb,
  first_response_at timestamptz, last_response_at timestamptz,
  collected_at timestamptz not null default now()
);
create index if not exists raw_llm_mentions_project_run on raw_llm_mentions (project_id, run_id);

create table if not exists raw_llm_responses (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  run_id uuid not null, platform text not null, model_name text, prompt text not null,
  response text, citations jsonb, fan_out_queries jsonb, cites_project boolean not null default false,
  web_search boolean, input_tokens int, output_tokens int, cost_usd numeric,
  collected_at timestamptz not null default now()
);
create index if not exists raw_llm_responses_project_run on raw_llm_responses (project_id, run_id);

do $$
declare t text;
begin
  for t in select unnest(array['raw_ai_keyword_metrics','raw_llm_mention_metrics','raw_llm_mentions','raw_llm_responses'])
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

grant select, insert, update, delete on raw_ai_keyword_metrics, raw_llm_mention_metrics, raw_llm_mentions, raw_llm_responses to seo_worker;
-- the console shows AI visibility: aggregate mention metrics and which prompts cite the site
grant select on raw_llm_mention_metrics, raw_llm_responses to seo_console;
