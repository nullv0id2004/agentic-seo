-- 0009: AI visibility measures this project's brand, not every brand that shares its name.
-- 1. projects.brand_context_terms: words that disambiguate the brand (first one qualifies it in prompts).
-- 2. raw_llm_mentions.target_kind / about_project: each search_mentions row records which target found it
--    and whether the answer is about this project (cites a project domain or names a context term).
alter table projects add column if not exists brand_context_terms text[] not null default '{}';
alter table raw_llm_mentions add column if not exists target_kind text;
alter table raw_llm_mentions add column if not exists about_project boolean;
