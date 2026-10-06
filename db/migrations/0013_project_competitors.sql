-- 0013_project_competitors.sql
-- projects.competitors: the domains this project competes with, from config/projects/*.yaml.
-- Keyword discovery pulls what these rank for; the off-page analyst never pitches them. Guessing them from
-- search results picked a business directory and LinkedIn's Turkish subdomain (run be9ad355), so they are
-- configured, not inferred.
alter table projects add column if not exists competitors text[] not null default '{}';
