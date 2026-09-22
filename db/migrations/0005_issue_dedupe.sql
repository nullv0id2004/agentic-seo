-- 0005_issue_dedupe.sql
-- One row per (project, issue) across runs. Before this, every audit inserted its findings afresh, so a
-- daily schedule would have added the same open issues every day. fingerprint = sha256(issue_type|url),
-- or sha256(issue_type|evidence) for issues on a protected route whose url is never spelled out.
-- run_id stays the run that first found the issue; last_run_id / last_seen_at / seen_count track recurrence.
alter table issues
  add column if not exists fingerprint text,
  add column if not exists last_run_id uuid,
  add column if not exists last_seen_at timestamptz not null default now(),
  add column if not exists seen_count int not null default 1,
  add column if not exists resolved_at timestamptz;

update issues
   set fingerprint = encode(sha256(convert_to(issue_type || '|' || coalesce(url, evidence), 'UTF8')), 'hex'),
       last_run_id = coalesce(last_run_id, run_id),
       last_seen_at = created_at
 where fingerprint is null;

-- keep the newest row of any duplicate set so the unique index can be built
delete from issues older
 using issues newer
 where older.project_id = newer.project_id and older.fingerprint = newer.fingerprint
   and (older.created_at, older.id) < (newer.created_at, newer.id);

alter table issues alter column fingerprint set not null;
create unique index if not exists issues_project_fingerprint on issues (project_id, fingerprint);
create index if not exists issues_project_status_seen on issues (project_id, status, last_seen_at desc);
