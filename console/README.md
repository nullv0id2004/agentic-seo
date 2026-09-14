# SEO console

Next.js 15 App Router. Deployed on Vercel. Connects to the SEO database as the `seo_console` role:
SELECT on derived tables, UPDATE on `approvals.status` and INSERT on `audit_log`, nothing else. It never
reads `raw_fetched_documents` and never holds a credential to anything but this database.

Every query runs inside a transaction that pins `app.project_id`, so RLS limits it to one project, and the
query helper refuses any statement without an explicit `project_id` filter.

Approvals are decided here; they are executed by the worker's executors only after the row reads `approved`.
