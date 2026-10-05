# SEO console

Next.js 15 App Router, standalone container on Azure App Service. Connects to the SEO database as the
`seo_console` role: SELECT on derived tables and on the raw tables its pages chart (migrations 0006 and
0010), UPDATE on `approvals.status` and INSERT on `audit_log`, nothing else. It never reads
`raw_fetched_documents` and never holds a credential to anything but this database.

Every query runs inside a transaction that pins `app.project_id`, so RLS limits it to one project, and the
query helper refuses any statement without an explicit `project_id` filter.

Approvals are decided here; they are executed by the worker's executors only after the row reads `approved`.

## Project page

One page per project, in tabs, with a 7 / 28 / 90 day range filter that scopes every number on the tab:

| Tab | Shows | From |
|---|---|---|
| Overview | KPI tiles with change vs the previous range, daily clicks, impressions, sessions, position, pipeline health, data freshness, latest events | runs, raw_gsc_performance, raw_ga4_daily, raw_llm_responses |
| Changes | Every decided approval: where it acts, what changed from what, PR link, failure reason, and whether a later audit confirmed the fix on the site | approvals, issues, runs |
| Search | Clicks, impressions, CTR, position per day; pages and queries vs the previous range | raw_gsc_performance |
| Traffic | Sessions and engagement per day, channels, landing pages | raw_ga4_daily |
| Keywords | Tracked keywords with volume, difficulty, CPC, AI volume, SERP rank, Search Console stats, mapped page | keywords, raw_keyword_metrics, raw_ai_keyword_metrics, raw_serp |
| Rankings | Latest rank per query, previous rank, history, AI Overview presence and whether it cites the site | raw_serp |
| Technical | Audit summary, on-page checks, open issues, protected-route probe, Google index status, vitals, schema, every page | raw_crawl_pages, raw_sitemap_urls, raw_vitals, issues |
| AI visibility | Weekly prompts and whether answers cite the site, answers and sources, domain mentions, brand mentions about this site | raw_llm_* |
| Content & outreach | On-page suggestions, briefs, pitches, backlinks | onpage_suggestions, content_briefs, pitches, mentions |
| Operations | Spend vs cap, spend per day (data APIs vs AI models), cost by agent, runs with per-agent detail, gaps, gate findings, audit log | runs, agent_logs, collection_gaps, gate_results, audit_log |

Raw tables keep every collection, so the queries in `lib/sql.ts` keep one run per date (the latest).
Days with no collection render as gaps in the charts, never as zeros. Every chart has a table view.

## From approval to the live site

Approving a fix does not change the site by itself. The worker opens a pull request on a fix branch in
the project's repository (never merging, never touching the default branch). A developer or coding agent
implements and merges it, the site's own pipeline deploys it, and the post-deploy audit then marks the
issue resolved. The Changes tab shows each step. An action that fails three times is marked `failed`
(migration 0011) and is not retried.
