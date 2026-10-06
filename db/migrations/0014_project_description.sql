-- 0014_project_description.sql
-- projects.description: what the project offers and to whom, in its own words (config/projects/*.yaml).
-- The keyword analyst judges each keyword's relevance against it. Without it, run be9ad355 kept
-- "steve jobs apple", "sarkari job" and employer brand searches as content opportunities for a
-- confidential hiring platform for senior professionals.
alter table projects add column if not exists description text;
