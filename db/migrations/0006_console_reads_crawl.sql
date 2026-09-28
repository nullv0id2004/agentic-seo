-- 0006_console_reads_crawl.sql
-- The console's project page shows the last probe of the protected paths, which is a read of
-- raw_crawl_pages. seo_console may read that table; it still cannot write any raw_* table
-- (Section 13.4: only collectors write raw rows) and RLS still scopes it to app.project_id.
grant select on raw_crawl_pages to seo_console;
