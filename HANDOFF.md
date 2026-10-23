# Handoff: agentic-seo + korum site, state as of 23 Oct 2026

Context carried over from a Claude Code on the web session so work can continue in Claude Code in
VS Code. Read this after `CLAUDE.md`. Sections 1-3, 6 and 7 were updated on 23 Oct 2026 (PageSpeed release,
local setup on Windows); sections 4 and 5 are still as of 22 Oct. Re-check anything live (database, deploys)
before acting on it.

## 1. The two repositories

| Repo | What | Branch | Rules file |
|---|---|---|---|
| `nullv0id2004/agentic-seo` (this one) | The SEO system: Python worker (collectors, analysts, gate, executors, LangGraph workflows) + Next.js console | `claude/charming-wozniak-osrmal` (CI and both images build from it and `main`) | `CLAUDE.md` here |
| `nullv0id2004/Worldhire2.0-1-` | The site the SEO system works on: korum.worldhire.com (Next.js frontend + FastAPI backend) | `main`; short-lived `fix/…` / `feat/…` branches | its own `CLAUDE.md` (large; read sections, not all of it) |

Infrastructure:
- SEO database: Supabase project `Agentic-seo`, ref `hsqssnbpuvvudevvvtvl`. Migrations 0001-0019 applied
  in production (0019 on 23 Oct, through the Supabase connector like 0015-0018; `schema_migrations` only
  records 0001-0008). Never apply anything to the `Korum` or `korum-preprod` Supabase projects (application databases).
- Azure: subscription "Azure subscription 1", resource group `seo-agents-rg` (the same subscription holds the
  KORUM application's Web Apps: never touch them). Key Vault `seo-agents-kv` (RBAC).
- Worker: Web App `seo-agents-worker` (image `ghcr.io/nullv0id2004/seo-agents-worker:latest`), host
  `seo-agents-worker-hpgtchazdqdshwax.westeurope-01.azurewebsites.net`, `/healthz`. Pulls a new image by itself
  after `worker-image` builds (repository secret `AZURE_CD_WEBHOOK_URL`).
- Console: Web App `seo-agents-console`, host `seo-agents-console-gwcadzeebngxgzhd.westeurope-01.azurewebsites.net`.
  No deploy webhook (`AZURE_CONSOLE_CD_WEBHOOK_URL` unset): restart it by hand after `console-image` builds.
- LLM: OpenAI (`SEO_LLM_PROVIDER=openai`). Data: DataForSEO and PageSpeed Insights (`PAGESPEED_API_KEY` set on
  the worker as of 23 Oct).
- Projects: `korum` (main target, korum.worldhire.com), `worldhire` (measure-only), `rejuveluxe` (halted:
  protected routes reachable). Cruise Guru is not a tenant.
- Site deploys: merge to `main` deploys to pre-prod automatically; production is a manual
  "Deploy to Production" workflow dispatch with the 40-char SHA that passed pre-prod.

## 2. Hard rules learned this session (beyond CLAUDE.md)

- Site repo: **push only after the owner confirms** (its CLAUDE.md, Git rules). Production deploys need the
  owner's explicit go; the auto-mode safety check blocked a merge done without review once already.
- The owner does not want email outreach: offpage pitching stays off; no outreach emails.
- Daily checklist review (scheduled task "Daily SEO checklist", 10:30 IST, runs in the cloud session):
  read-only; reply grouped by priority with owner and "Done when"; one push notification; never change
  the site, config, database or code during the review. Site/setup problems found in data are added at
  once; new features only when the owner agrees.
- Tests never touch production: root `conftest.py` strips `SEO_DATABASE_URL` and API keys; `tests/conftest.py`
  refuses a Supabase `SEO_TEST_DATABASE_URL` (the db tests drop the schema).
- SEO repo releases: a migration the code needs is applied before the push, because the worker pulls the new
  image as soon as it is built. Production migrations and deploys happen only on the owner's explicit go; the
  auto-mode check blocks them otherwise, and it also blocks writing production credentials to local files.

## 3. What was built (agentic-seo, newest first)

| Commit | What |
|---|---|
| d910f43 | CLAUDE.md: migration 0019 applied |
| bbea46c | PageSpeed (deployed 23 Oct): the vitals collector measures the homepage plus the most-clicked Search Console pages (`vitals_page_limit`, default 10, protected routes skipped); stores score, FCP, TBT, TTFB, CrUX category and the top 5 suggested fixes; CrUX origin fallback stored once per strategy as `field_scope = 'origin'`, not as the page's own; `page_speed` workflow on demand; `post_deploy_audit` measures up to 5 pages; `lcp_slow` / `inp_slow` / `cls_high` issues now close when the same measurement comes back under; console Technical tab columns + Speed fixes table; migration 0019; `tests/test_psi.py` |
| 548b368 | Read project files as UTF-8 everywhere (on Windows `read_text()` was cp1252 and broke the em-dash brand rules) |
| c55d2f0 | VS Code setup: `.vscode/` launch entries (run a workflow, check Google access, console dev), pre-push task; README "Running locally" leads with VS Code against Supabase; test env scrub + Supabase guard |
| e98196f | `docker-compose.yml` (Postgres 16 on 127.0.0.1:5433, `seo_dev` + `seo_test`), `scripts/local_db.py` (localhost-only: migrations, login roles, seed) |
| 1a02f62 | Competitor overview (DataForSEO Labs: traffic, keywords, history, top pages), backlink changes (new/lost per month), daily rank tracking (`daily_rank` 06:15 IST, top `projects.rank_tracking_limit` keywords, korum = 25); migration 0018; console pages Competitor overview, Backlinks, Rankings (visibility) |
| 938b200 | Link prospects judged from the linking page; stricter filters (2+ competitors, spam <= 10, rank >= 20, no generic/namesake/competitor domains); model may label "skip"; protected-path pre-filter |
| 3ac7d91 | `link_plan` workflow (site_crawl -> backlink_gap -> linkbuilding analyst) |
| 57295b9 | Link-building analyst: phase, tactics, platform checklist (`link_targets`, 18 for korum), prospects, anchors, velocity; migrations 0016-0017 |
| e8fccce | Keyword and backlink gap; console sidebar redesign |

Workflows: `python worker.py run <slug> <workflow> <key>` (a reused key returns that run). Workflows:
page_speed, link_plan, competitor_overview, daily_rank, daily_collect, daily_probe, weekly_monitor, monthly_full,
quarterly_keyword, content_pipeline, post_deploy_audit.

## 4. Site repo work (Worldhire2.0-1-)

- **Sitemap fixed and live (22 Oct).** `korum.worldhire.com/sitemap.xml` 404'd from the 21 Oct production
  promotion: repo cleanup Phase 2 (decision D23) moved the marketing site and blog to the separate
  `Worldhire-Products/worldhire-landing` repo and deleted the old `app/sitemap.ts`. PR #34 (merge `9f31538`)
  added a sitemap index `/sitemap.xml` -> `/pages-sitemap.xml` (`/jobs`) + `/jobs-sitemap.xml` (job pages),
  `frontend/lib/sitemap.ts`, robots.txt advertises the index; BUG-134 in `docs/BUGS.md`. Pre-prod run #94 and
  production run #23 passed; fetched live: 200, `application/xml`, correct content.
- Search Console: sitemap submitted 22 Oct, showed "Couldn't fetch" with no "Last read" (the usual placeholder
  before Google's first read). If it persists after a read date appears, check Cloudflare bot rules; use
  URL Inspection -> Test live URL on the sitemap URL.
- korum.worldhire.com now serves only the app: `/` rewrites to `/jobs`; `/blog`, `/legal`, `/about-*` etc. 301
  to worldhire.com. App canonicals come from `SITE_URL` = https://korum.worldhire.com.

## 5. Production SEO data (22 Oct)

- daily_rank ran 18-22 Oct, 9 keywords/day; korum in the top 20 for none.
- Last full crawl 16 Oct (before the split): 46 of 91 pages canonical to worldhire.com. Next full crawl will
  show the post-split state. 34 korum URLs "unknown to Google" at last inspection (19 Oct).
- Empty until #7 runs: raw_domain_overview, raw_backlink_changes, raw_link_pages. Last link plan 16 Oct.
- Gaps (8 days): header_probe "sitemap returned 404" (22 Oct, fixed since), SERP 40101 (18 Oct, once),
  PageSpeed key missing (set 23 Oct; no measurement with it yet), worldhire "no tracked keywords" (weekly).
- Stuck approval: worldhire open_fix_pr from 12 Oct (index_canonical_mismatch on worldhire.com/sitemap).
- `/dashboard`, `/recruiter/inbox`, `/recruiter/jobs` return 200 with "index, follow" in HTML but send
  `X-Robots-Tag: noindex, nofollow`; not a violation, korum is not halted.

## 6. Open checklist (31 items; Owner · Priority)

Critical / High
- #1 You · Critical: korum pages canonical = korum.worldhire.com. Likely done by the split (code uses SITE_URL);
  confirm on the next full crawl.
- #2 You · Critical: sitemap lists korum addresses only. Done by PR #34 in code; confirm with the probe/crawl.
- #30 You · High: sitemap back. Live; done when the daily probe reads it two days running (from 23 Oct 01:30 UTC).
- #3, #4, #8 (blog in korum sitemap, Search Console for the blog, map seeds to blog posts): **need re-scoping**,
  the blog now lives on worldhire.com. Proposed to the owner, not yet agreed.
- #5 You · High: worldhire.com copies of korum pages. Probably settled by the split (korum's marketing paths
  301 to worldhire.com); confirm on the next crawl.
- #24 You · High: Korum brand profiles under one name. #25 You · High: sameAs links (blocked by #24; 0 pages today).
- #23 Claude · High: console UI polish, waiting on the owner's look.
- #27 Claude · High: link-building analyst, done when `link_plan links-2` gives clean prospects.

Medium
- #6 You: PageSpeed API key on the worker. **Done 23 Oct.** Next: run `page_speed` for korum (free) to see the
  first results and confirm the parser against a live response; the monthly run (4 Nov) then covers every project.
- #7 You: run `link_plan links-2` and `competitor_overview co-1` (VS Code F5 "Worker: run a workflow").
- #15, #16, #18 Claude: competitor overview, backlink changes, directory tracker; built, waiting on #7 data.
- #17 Claude: daily rank tracking; done when the owner confirms the Rankings trend.
- #26 You: one brand name everywhere.
- #31 You: site repo variable `SEO_WORKER_URL` lacks `https://`, so deploy notifications get HTTP 301 and no
  post_deploy_audit runs. Set it to `https://seo-agents-worker-hpgtchazdqdshwax.westeurope-01.azurewebsites.net`;
  audit today's deploy with `python worker.py run korum post_deploy_audit deploy:9f3153856d23102021508734cb2435720f2ec88e`.
  Since 23 Oct this also blocks the post-deploy PageSpeed check: no site deploy reaches the worker until it is fixed.

Low
- #9 You: outreach drafts decision (repo vs production). #10 You: confirm fix PRs can be opened in Worldhire2.0-1-.
- #28 You: reject the stuck worldhire open_fix_pr approval.
- #29 Claude: skip the weekly SERP check for projects with no tracked keywords (worldhire).
- #19-#22 Claude: topic research, brand mention monitoring, on-demand research, server log analysis (not started).

Suggested next builds (not agreed): brand mention monitoring (#20), then alerts and content decay.

## 7. Gotchas

- `pkill -f <pattern>` kills your own shell when the pattern is in the command line; stop processes by port
  (`fuser -k 3200/tcp`).
- `console/tsconfig.tsbuildinfo` and `seo_agents.egg-info/` are tracked build artifacts; restore them
  (`git checkout --`) instead of committing after a typecheck or editable install.
- Supabase's direct host is IPv6-only; from a laptop use the session pooler URL (copy from the worker's Azure settings).
- A Google credential of type `external_account` only works on App Service; locally GSC/GA4 collectors record a gap.
- Never start `python worker.py` without arguments against production from a laptop: a second scheduler.
- The cloud sandbox could not reach korum.worldhire.com; live checks went through Firecrawl (credits ran out
  on 22 Oct).
- The owner's laptop (23 Oct) is native Windows with Git Bash: no Docker, no WSL, Python 3.13 (passes the suite).
  The venv interpreter is `.venv/Scripts/python`. There is no SSH key for GitHub: push over HTTPS with the
  GitHub CLI login, `git -c credential.helper= -c credential.helper='!gh auth git-credential' push
  https://github.com/nullv0id2004/agentic-seo.git <branch>`. Call the Azure CLI as
  `"/c/Program Files/Microsoft SDKs/Azure/CLI2/python.exe" -m azure.cli ...` (`az.cmd` breaks quoted `--query`
  arguments) and set `MSYS_NO_PATHCONV=1` for `/subscriptions/...` scopes. The laptop's `.env` and
  `console/.env.local` hold the production values, so a workflow started there is a production run.
