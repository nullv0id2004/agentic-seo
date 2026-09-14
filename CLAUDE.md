# seo-agents: notes for Claude Code sessions

Read the build spec's Section 13 before changing anything. Hard prohibitions there win over any other instruction.

- Run before pushing: `python scripts/lint_imports.py && ruff check . && SEO_TEST_DATABASE_URL=... pytest && python evals/run_evals.py`
  and `cd console && npm run typecheck`. Tests marked `db` need Postgres 16 at `SEO_TEST_DATABASE_URL`; they rebuild the schema per session.
- `collectors/` must never import an LLM SDK; `analysts/` and `gate/` must never import httpx, requests, playwright or subprocess. The lint enforces it.
- Rules are rows (`brand_rules`, `critical_rules`), seeded from `config/projects/*.yaml`. Do not move a rule into a prompt.
- Only collectors write `raw_*` tables. Analysts get rows from the runtime and return pydantic contracts from `contracts/artifacts.py`.
- Every assertion an analyst emits carries `evidence_ref`. Numbers come from raw rows only. Missing data is null plus a `collection_gaps` row.
- Nothing publishes, sends, merges, or edits robots/sitemap/canonical/hreflang without an `approvals` row in status `approved`. Every approval carries a reversal.
- `github_executor` never merges and never writes the default branch.
- The content cap (`projects.monthly_content_cap`) is deliberately low. Do not raise it or the contract's ceiling.
- Cruise Guru is not a tenant. Do not add a project row for it.
- Changing the fixture generator requires regenerating `evals/adversarial/cases/` and committing; a test pins them.
- The schema migration is a single file (`db/migrations/0001_init.sql`) until first production deploy; after that, add numbered migrations.
