-- 0010_console_reads_raw.sql
-- The console shows search performance, traffic, rankings, keyword data, vitals, sitemap coverage,
-- Google update status and AI mentions straight from the raw rows the reports are built on, so the
-- numbers on screen and in a report never disagree. seo_console gets read access to those tables.
-- It still writes no raw_* table (Section 13.4), and RLS still scopes every read to app.project_id.
-- raw_fetched_documents stays unreadable: it holds untrusted third-party page text.
grant select on raw_gsc_performance, raw_ga4_daily, raw_sitemap_urls, raw_vitals, raw_serp,
  raw_keyword_metrics, raw_search_status, raw_ai_keyword_metrics, raw_llm_mentions to seo_console;
