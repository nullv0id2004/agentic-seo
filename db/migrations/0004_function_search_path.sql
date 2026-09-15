-- 0004_function_search_path.sql
-- Pin search_path on the RLS helper (Supabase lint 0011). The body only reads a GUC, but a pinned
-- path removes any chance of a role-level search_path redirecting name resolution.
alter function current_project_id() set search_path = public;
