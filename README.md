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

Needs Python 3.12 and Node 22. On Windows, use WSL2 (the commands below are bash), or work natively from Git Bash:
Python 3.12 or 3.13 both pass the suite; the venv's interpreter is `.venv/Scripts/python`, not `.venv/bin/python`;
and without Docker the db tests can use the portable PostgreSQL 16 binaries (EDB's zip: `initdb -U postgres -A trust`,
then `pg_ctl -o "-p 5433" start` and create `seo_test`). Git Bash rewrites arguments that look like paths, so set
`MSYS_NO_PATHCONV=1` before `az` commands that take a `/subscriptions/...` scope.

### Working from VS Code against the production database

```bash
git clone https://github.com/nullv0id2004/agentic-seo.git && cd agentic-seo
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install -e ".[dev]"     # the worker image's pinned set, then pytest and ruff
cp .env.example .env
cd console && npm ci && cp .env.example .env.local && cd ..
code .                                                           # accept the recommended extensions
```

Fill `.env` from the worker Web App's application settings (Azure portal: Settings, Environment variables):
`SEO_DATABASE_URL` (the `seo_worker_login` URL through the Supabase session pooler; the direct
`db.<ref>.supabase.co` host is IPv6-only and fails on most home networks), `SEO_LLM_PROVIDER`, `OPENAI_API_KEY`,
`SEO_AGENT_MODELS`, `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD`, `PAGESPEED_API_KEY`, `SEO_SECRET_BACKEND` and
`AZURE_KEY_VAULT_URL`. Key Vault is read with your own Azure login (`az login`), which needs permission to read
the vault's secrets. Search Console and GA4 then work only if the stored Google credential is a service-account
key: an `external_account` credential exchanges App Service's managed identity, so on a laptop those two
collectors record a gap. Fill `console/.env.local` from the console Web App's settings.

In Run and Debug (F5):

| Entry | Does |
|---|---|
| Worker: run a workflow | asks for project, workflow and run key, loads `.env`, stops at breakpoints |
| Worker: check Google access | `worker.py check-google`: does the credential resolve and see the Search Console and GA4 properties; writes nothing |
| Console: next dev | the console at `http://localhost:3000/?token=<CONSOLE_ACCESS_TOKEN>` (Chrome, Edge or Firefox: the sign-in cookie is `Secure`, which they accept on localhost) |

The Testing panel runs pytest, and Terminal, Run Task, **Checks before pushing** runs the CLAUDE.md list.

A run started here is a production run: it writes production rows, spends from the project's budget, and runs
the code in your checkout rather than the deployed image. Code reaches App Service only by a push (the image
workflows build on pushes to `main` and `claude/charming-wozniak-osrmal`) and a restart. There is deliberately
no entry for `python worker.py` without arguments: that serves the webhooks and starts a second scheduler beside
the one on App Service. In a plain terminal, load `.env` first: `set -a && . ./.env && set +a`. Approvals
decided in a local console are real too.

Tests never see `.env`: the root `conftest.py` strips the database URL and the API keys, and the `db` tests
refuse a Supabase URL, since they drop the schema.

### Database tests and a local database (optional)

```bash
docker compose up -d       # Postgres 16 on 127.0.0.1:5433 with seo_dev and seo_test
SEO_TEST_DATABASE_URL=postgresql://postgres@localhost:5433/seo_test pytest
python scripts/local_db.py # seo_dev: migrations, login roles (password "local"), projects; prints the URLs
```

Tests marked `db` need a Postgres reachable at `SEO_TEST_DATABASE_URL` (add it to `.env` for the Testing panel);
they rebuild the schema per session. Without it they skip, and CI runs them on every push. Point `.env` and
`console/.env.local` at the URLs `local_db.py` prints to try a change without touching production; that
database starts empty except for the projects, and a workflow calling DataForSEO or a model still spends real
money.

Checks before pushing:

```bash
python scripts/lint_imports.py && ruff check .
SEO_TEST_DATABASE_URL=postgresql://postgres@localhost:5433/seo_test pytest
python evals/run_evals.py            # deterministic + gullible fake verifier
python evals/run_evals.py --live     # with the real verifier (ANTHROPIC_API_KEY)
cd console && npm run typecheck
```

## Deploying

1. Create a **new, dedicated** Supabase project for the SEO system. Never install this schema into an application
   database (Section 13.1): the KORUM production and preprod projects are off limits.
   Apply the migrations in order (`db/migrations/0001..0019`), either with the CLI or by pasting each file into
   the SQL editor; they are plain SQL and idempotent. A migration a release depends on goes in before the push:
   the worker image is pulled as soon as it is built. Then create the login roles and seed the projects:
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
| `post_deploy_audit` | deploy webhook (`/webhooks/deploy` with bearer `DEPLOY_WEBHOOK_SECRET`, or Vercel's signed hook), keyed on deployment id | header_probe, site_crawl (delta), vitals (up to 5 pages), technical_analyst, gate, approvals |
| `daily_probe` / `daily_collect` | cron 07:00 / 05:30 IST | header_probe on critical paths / gsc_performance for one day |
| `weekly_monitor` | cron Mon 06:00 IST | serp, search_status, trend_analyst, gate, report append |
| `monthly_full` | cron 4th 06:00 IST | all collectors, all enabled analysts, gate, report, approvals digest |
| `quarterly_keyword` | cron 2nd of Jan/Apr/Jul/Oct | keyword_metrics, keyword_analyst, gate, keyword_mapping approval |
| `content_pipeline` | manual: `python worker.py run <slug> content_pipeline` | doc_fetch, content_analyst, gate stages 1 and 2, publish approval |
| `daily_rank` | cron 06:15 IST, projects with `rank_tracking_limit` > 0 | serp for the top tracked keywords (mapped first, then by volume) |
| `competitor_overview` | manual: `python worker.py run <slug> competitor_overview <key>` (monthly_full runs it too) | competitor_overview collector |
| `link_plan` | manual: `python worker.py run <slug> link_plan <key>` | site_crawl, backlink_gap, linkbuilding_analyst, gate (refreshes the link plan between monthly runs) |
| `page_speed` | manual: `python worker.py run <slug> page_speed <key>` | vitals for the top pages (measure now; issues are raised and closed by the monthly and deploy runs) |

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

## Competitive research (keyword and backlink gap)

- For a project with configured `competitors`, the monthly run also runs `keyword_discovery` and
  `backlink_gap`. Discovery stores, besides seed ideas and competitors' topic keywords, what the project's own
  hosts rank for (`raw_keyword_ideas.source = 'own'`, unfiltered). `backlink_gap` stores the strongest referring
  domains of each competitor and all of the project's own (`raw_referring_domains`, migration 0015).
- The console's Competitive research page computes both gaps from those raw rows when shown: keywords
  competitors rank for that the project is missing (2+ competitors), has untapped (1), is weak on (ranks below
  the best competitor) or strong on; and domains linking to competitors but not to the project, excluding its
  own links and sites that link to everything. Every table there can be sorted, filtered and exported as CSV.

## Link building

The `linkbuilding` analyst (monthly, or on demand with the `link_plan` workflow, when it is in `enabled_agents`) turns a link-building playbook into a plan
built from the project's own rows. It never drafts outreach and nothing it produces needs an approval: it is advice.

- Data: `backlink_gap` also stores one authority summary per target (`raw_backlink_summary`: domain rank, referring
  domains, when the first link was seen) and the anchors of links to the project (`raw_anchors`), from DataForSEO
  `backlinks/summary/live` and `backlinks/anchors/live` (migration 0016). It runs for a project without competitors
  too when `linkbuilding` is enabled, so the plan always has the project's own links.
- The checklist is rows: `link_targets`, seeded from `link_targets:` in `config/projects/<slug>.yaml` (platform,
  domain, kind `profile` / `directory` / `review`, priority, note). A profile is done when the site's Organization
  schema lists it in `sameAs`; a directory or review site is done when it lists or links to the site.
- Computed in code: the authority phase (foundation, growth, authority: the lower median of domain rank, age of the
  first link, indexable pages and listings done; a site DataForSEO knows no links to counts as foundation), the three
  tactics that fit it, gap prospects (`rules/links.py`: sites linking to two or more competitors and not to the
  project, spam score at most 10, domain rank at least 20, no namesakes, generic platforms or competitors), anchor
  shares against natural ranges (exact match above 5% is flagged), and new referring domains per month (50 or more
  in a month is flagged).
- For the strongest 80 gap candidates `backlink_gap` stores one page on each that links to a competitor
  (`raw_link_pages`, migration 0017: address, title, anchor, platform type, place on the page), grouped by competitor
  so at most six `backlinks/backlinks/live` calls cover them.
- The model labels each prospect from that page (directory, resource page, blog, news, forum) with one line on how to
  earn the link, or skips it as not a real opportunity (job-listing scrapers, spam, unrelated sites); and drafts 50,
  150 and 300 word profile descriptions from the project description and homepage. Text with a number it was not
  given, a web address, a missing brand name, over-length copy, a blocking brand rule or a protected path is dropped;
  so is a third-party page address or title on a protected path.
- Stored in `link_plans` (one per run) and `link_opportunities` (one per domain across runs; a prospect that starts
  linking to the project becomes `won`). The console's Link building page shows both.

## Competitor overview, backlink changes, rank tracking

- **Competitor overview** (`competitor_overview` collector, monthly for projects with competitors, or on demand):
  DataForSEO Labs `domain_rank_overview`, `historical_rank_overview` (12 months) and `relevant_pages` (top 10) for the
  project and each competitor, Google India in English, into `raw_domain_overview`, `raw_domain_history` and
  `raw_domain_pages` (migration 0018). A domain Labs has no data for gets a gap row and a dash in the console, not a
  zero. The console's Competitor overview page puts them side by side with domain rank and referring domains.
- **Backlink changes** (`backlink_gap`): the project's own referring domains are collected live and lost (`lost_date`
  set), and `backlinks/timeseries_new_lost_summary` gives new and lost backlinks and referring domains per month for
  the project and each competitor (`raw_backlink_changes`). The Backlinks page shows linking domains (new, live,
  lost), spam scores (above 30 worth checking, above 60 likely toxic), the monthly trend and competitors' link
  velocity.
- **Daily rank tracking**: `rank_tracking_limit` in `config/projects/<slug>.yaml` (0 to 50, default 0) is how many
  tracked keywords the `daily_rank` workflow checks every day (Google India, desktop, top 20); the weekly monitor
  still checks every keyword. The Rank tracking page shows visibility (the CTR-weighted share of the clicks the
  tracked keywords could bring), each keyword's daily trend and best competitor, and who else ranks for them.

## Page speed (PageSpeed Insights)

The `vitals` collector (`collectors/psi.py`) calls the PageSpeed Insights API for mobile and desktop and stores
one lab row (Lighthouse: performance score, LCP, FCP, TBT, CLS, server response time, the five failing audits with
the largest estimated saving) and one field row (CrUX, real Chrome users over 28 days) per page in `raw_vitals`
(migration 0019). A page with too little traffic gets CrUX's numbers for the whole site; those are stored once per
strategy against the origin (`field_scope = 'origin'`), never as the page's own.

- **Which pages:** the homepage, then the pages with the most Search Console clicks in the last 28 days, up to
  `vitals_page_limit` in `config/projects/<slug>.yaml` (1 to 25, default 10). A deploy audit puts the changed pages
  right after the homepage and measures at most 5. Protected routes are never measured.
- **When:** the monthly run, every post-deploy audit, and on demand with the `page_speed` workflow.
- **Issues:** the technical analyst raises `lcp_slow`, `inp_slow` and `cls_high` above 2,500 ms, 200 ms and 0.1.
  One closes when a later run measures the same page the same way (strategy, lab or field) and no longer flags it.
- **Key:** in Google Cloud Console, enable the PageSpeed Insights API (APIs & Services, Library), create an API key
  (Credentials, Create credentials, API key) and restrict it to that API. Do not add a website restriction: the
  worker calls from a server, with no referrer. Set it as `PAGESPEED_API_KEY` in the worker Web App's application
  settings (and in `.env` locally). Without it the collector records a gap and measures nothing.

The console's Technical tab shows the latest measurement per page and a Speed fixes table.
