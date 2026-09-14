-- 0002_roles.sql
-- The worker role. NOLOGIN; each deployment creates a LOGIN role that is a member of it.
-- It does not have BYPASSRLS. It has no superuser. It never holds a credential to any
-- application database (Section 13.1): this role exists only in the SEO database.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'seo_worker') then
    create role seo_worker nologin;
  end if;
end $$;

grant usage on schema public to seo_worker;
grant select, insert, update, delete on all tables in schema public to seo_worker;
grant execute on function current_project_id() to seo_worker;
grant execute on function list_active_projects() to seo_worker;

-- Console read-only role: reads derived tables and approvals for the Next.js console; never raw_fetched_documents text.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'seo_console') then
    create role seo_console nologin;
  end if;
end $$;
grant usage on schema public to seo_console;
grant select on projects, issues, reports, approvals, runs, gate_results, collection_gaps, agent_logs, content_briefs, keywords, pages, mentions, brand_rules, critical_rules to seo_console;
grant update (status, approver_id, decided_at) on approvals to seo_console;
grant execute on function current_project_id() to seo_console;
grant execute on function list_active_projects() to seo_console;
