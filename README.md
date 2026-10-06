# seo-agents

Multi-project agentic SEO system. Deterministic collectors, tool-less LLM analysts, a two-stage gate,
and an approval queue in front of every action. Built to the v1.0 specification, milestone by milestone.

Stack: Python 3.12, LangGraph with a Postgres checkpointer, Supabase Postgres, Next.js 15 console on
Vercel, worker on Azure App Service.

## Layout

```
config/projects/*.yaml   one file per project: seeds projects, brand_rules, critical_rules
collectors/              deterministic, has network, no LLM; writes raw_* tables only
analysts/                LLM nodes; read rows the runtime hands them; no network
rules/                   pure rule evaluation shared by collectors, analysts and the gate
gate/                    stage1_rules.py (pure python) and stage2_verify.py (one tool-less model call)
executors/               github, cms, email; each with one job and one credential; every action reversible
orchestrator/            graphs/, runtime.py, budget.py, scheduler.py, runs.py, approvals.py, notify.py
contracts/               pydantic models: the only JSON shapes that cross a plane boundary
db/migrations/           single schema migration with RLS on every table, plus roles
evals/                   Section 14 adversarial suite and runner
console/                 Next.js console: read-only views and the approvals queue
worker.py                App Service entrypoint: webhook server + one-minute scheduler loop
```

## Invariants enforced in code, not prompts

| Rule | Where it lives |
|---|---|
| Collectors never import an LLM SDK; analysts never import a network client | `scripts/lint_imports.py`, run in CI |
| Only collectors write `raw_*` tables | `db/connection.py` refuses writes from any other caller |
| Every query carries a project scope | RLS keyed on `app.project_id`; `ProjectScope` refuses unscoped statements |
| Each agent writes only the tables it declared | `orchestrator/registry.py` grants, enforced by `ProjectScope` |
| Budget halts before dispatch | `orchestrator/budget.py`, called in `runner.run_workflow` before the graph starts |
| One run per (project, workflow, logical date) | unique constraint on `runs`; a duplicate trigger is skipped |
| Protected paths never appear in an artifact | gate stage 1 URL allowlist; the technical analyst cites the raw row instead |
| Analysts emit no number that is not in a raw row | keyword volumes copied from metric rows; report commentary rejected if it carries a foreign number; content sentences with unbacked figures stripped |
| Fetched documents are untrusted | `is_untrusted` check constraint; stage 2 scans for injection and requires a quoted span |
| Every approval carries its reversal | check constraint on `approvals.reversal_payload`; executors return the concrete reversal |
| `github_executor` never merges, never touches main | merge endpoints refused; writes go to `seo-fix/*` branches only |
| Content is capped per project per month | `projects.monthly_content_cap`, enforced in `analysts/content.py` before any model call; the contract rejects a cap above 8 |
| Cruise Guru is not a tenant | slug rejected by the contract and by a table constraint |
| An indexable protected route halts the project | `orchestrator/critical.py` sets `projects.halted_reason`; the runner skips every workflow except the probes until a probe comes back clean |
| A retry after a crash resumes, it does not duplicate | `runner.run_workflow` re-enters the checkpointed graph for a failed or stale run with the same logical date |

## Running locally

```bash
uv venv --python 3.12 .venv && . .venv/bin/activate
uv pip install -e ".[dev]"
python scripts/lint_imports.py
SEO_TEST_DATABASE_URL=postgresql://postgres@localhost:5432/seo_test pytest
python evals/run_evals.py            # deterministic + gullible fake verifier
python evals/run_evals.py --live     # with the real verifier (ANTHROPIC_API_KEY)
```

Tests marked `db` need a Postgres reachable at `SEO_TEST_DATABASE_URL`; they rebuild the schema per session.
Without it those tests skip.

## Deploying

1. Create a **new, dedicated** Supabase project for the SEO system. Never install this schema into an application
   database (Section 13.1): the KORUM production and preprod projects are off limits.
   Apply the migrations in order (`db/migrations/0001..0004`), either with the CLI or by pasting each file into
   the SQL editor; they are plain SQL and idempotent. Then create the login roles and seed the projects:
   ```bash
   SEO_DATABASE_URL=postgresql://postgres:...@db.xxx.supabase.co:5432/postgres python -m db.migrate
   psql "$SEO_DATABASE_URL" -c "create role seo_worker_login login password '...' in role seo_worker;"
   psql "$SEO_DATABASE_URL" -c "create role seo_console_login login password '...' in role seo_console;"
   python scripts/seed_projects.py                 # or: python scripts/seed_projects.py --print-sql | psql "$SEO_DATABASE_URL"
   ```
   The worker connects as `seo_worker_login`, which does not bypass RLS. Never point it at the Supabase service role.
   Acceptance check: connect as `seo_worker_login` and run `select count(*) from projects`; it must return zero.
2. Put the per-project Search Console service-account JSON, the GitHub fix-branch token, the CMS token and the
   SMTP credentials into Azure Key Vault under the names in `config/projects/*.yaml` and `executors/*.py`.
   The worker's managed identity gets `get` on secrets and nothing else. No component holds a credential to
   any application database (Section 13.1).
3. Worker: every push builds `ghcr.io/<owner>/seo-agents-worker:latest` (`.github/workflows/image.yml`). Point a Linux
   Web App for Containers at it with `WEBSITES_PORT=8080`, set `SEO_DATABASE_URL`, `AZURE_KEY_VAULT_URL`,
   `SEO_LLM_PROVIDER` with `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`, `PAGESPEED_API_KEY`, `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD`, `VERCEL_WEBHOOK_SECRET`.
   Point the Vercel deploy webhook (deployment.succeeded) at `https://<worker>/webhooks/vercel`.
4. Console: `console/Dockerfile` builds a standalone Next.js server; `.github/workflows/console-image.yml`
   pushes it to `ghcr.io/<owner>/seo-agents-console`. Run it as a container Web App with
   `CONSOLE_DATABASE_URL` (the `seo_console_login` role through the session pooler), `CONSOLE_ACCESS_TOKEN`,
   `CONSOLE_APPROVER_ID` (must equal the project's `approver_id`) and `WEBSITES_PORT=8080`.

## Workflows

| Workflow | Trigger | Nodes |
|---|---|---|
| `post_deploy_audit` | deploy webhook (`/webhooks/deploy` with bearer `DEPLOY_WEBHOOK_SECRET`, or Vercel's signed hook), keyed on deployment id | header_probe, site_crawl (delta), technical_analyst, gate, approvals |
| `daily_probe` / `daily_collect` | cron 07:00 / 05:30 IST | header_probe on critical paths / gsc_performance for one day |
| `weekly_monitor` | cron Mon 06:00 IST | serp, search_status, trend_analyst, gate, report append |
| `monthly_full` | cron 4th 06:00 IST | all collectors, all enabled analysts, gate, report, approvals digest |
| `quarterly_keyword` | cron 2nd of Jan/Apr/Jul/Oct | keyword_metrics, keyword_analyst, gate, keyword_mapping approval |
| `content_pipeline` | manual: `python worker.py run <slug> content_pipeline` | doc_fetch, content_analyst, gate stages 1 and 2, publish approval |

Projects are staggered twenty minutes apart so shared API keys are not hit at once. Approved rows are executed
on the next scheduler tick.

## Adding a project

Add `config/projects/<slug>.yaml` and run `scripts/seed_projects.py`. There is no property enum anywhere.
An external client gets its own Supabase project and worker deployment, not a row here.

## AI Optimization data (DataForSEO)

Three collectors, all fail closed without `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`:

| Collector | Workflow | Writes | Source endpoint |
|---|---|---|---|
| `ai_keyword_metrics` | quarterly_keyword | `raw_ai_keyword_metrics` | `ai_optimization/ai_keyword_data/keywords_search_volume/live` |
| `llm_mentions` | weekly_monitor | `raw_llm_mention_metrics`, `raw_llm_mentions` | `llm_mentions/target_metrics_lite/live` and `llm_mentions/search_mentions/live` |
| `llm_responses` | weekly_monitor | `raw_llm_responses` | `<platform>/llm_responses/live`, ChatGPT (forced web search) and Perplexity Sonar by default |
| `keyword_discovery` | quarterly_keyword | `raw_keyword_ideas` | `dataforseo_labs/google/keyword_ideas/live` and `ranked_keywords/live` for the top SERP competitors |

Prompts are built deterministically from the tracked keywords and the brand (`collectors/llm_responses.py`,
`build_prompts`), capped at 12 per platform per run. Branded keywords and one-edit misspellings of the brand
or domain labels are skipped, and ChatGPT is asked with `force_web_search` so every answer can cite.
`cites_project` is computed in the collector from the source domains.

Brands share names (KORUM is also a fishing tackle maker and an election app), so the project's
`brand_context_terms` (YAML, column added in migration 0009) disambiguate: the first term qualifies the
brand in prompts ("KORUM (WorldHire)"), and a brand mention row gets `about_project` only when it cites a
project domain or names one of the terms. The brand search returns at most 20 rows (`brand_mention_limit`), since
DataForSEO bills per row and most rows for a shared name are other brands; the domain search keeps 100. Aggregates come from the domain only. The report analyst emits
`llm_mentions_domain`, `llm_ai_search_volume_domain`, `llm_brand_answers_sampled`,
`llm_brand_answers_about_project`, `llm_prompts_asked` and `llm_prompts_citing_site`; the trend analyst
emits `ai_citation_change` when a prompt starts or stops citing the site. Location defaults to 2840 (United
States) because ChatGPT mention data exists only there; pass `ai_location_code` / `country_iso` as
collector params to change it.

DataForSEO reports its charge in every response. The shared HTTP client passes it to the collector, the
agent log records it, and so the run's `cost_usd` and the monthly budget check include API spend, not only
model tokens.

## Index coverage, keyword discovery and AI source gaps

- `gsc_inspection` stores Google's full verdict in `raw_url_inspection` (coverage state, last crawl,
  canonicals, sitemaps). The technical analyst turns every crawled, indexable page that Google has not
  indexed into an `index_*` issue named after Google's reason; robots, noindex and canonical problems are
  high (a fix PR can solve them), crawl and quality states are medium. Index issues close only on a later
  inspection that no longer reports them, never on a crawl alone.
- `keyword_discovery` (quarterly, before pricing) grows ideas from the seeds and tracked keywords, and
  pulls the keywords the project's configured `competitors` (YAML, migration 0013) rank for, restricted to
  the project's topic words: distinctive words from the seeds (words generic to the vertical excluded),
  and for a vertical with anchors also a second word that places the search in it ("executive job search",
  not "executive meaning"). Without configured competitors that part is skipped and search-result guesses
  are logged as suggestions only. The top 60 by volume, minus navigational and competitor-brand searches,
  join the priced universe. The keyword analyst judges each keyword against the project's `description`
  (migration 0014), on gpt-4.1 by default with OpenAI (`SEO_AGENT_MODELS` overrides per agent); only keywords
  it explicitly marks relevant, on topic, and that no page fits are kept unmapped in
  `keywords` as content opportunities (at most 40 per run), and an irrelevant keyword is never mapped.
- The technical analyst reads crawl rows only (URL Inspection rows share `raw_crawl_pages`), and a page
  whose canonical names another URL is not an index issue: not being indexed there is the intent.
- The off-page analyst never pitches the project's own registrable domain or a configured competitor, and
  drops a draft that cites a year before last year.
- The console's AI tab lists the sites AI answers and AI Overviews cite instead of the project: the
  places to get listed or quoted.
- An approver supplies a pitch's recipient in the console (`approvals.approver_input`, migration 0012);
  nothing is sent without one.
