-- 0011_approval_failed_status.sql
-- An approved action whose executor keeps failing (a pitch with no recipient, a missing GitHub token) was
-- retried every scheduler minute forever. After three attempts the worker now marks it 'failed': it is not
-- executed, nothing changed in the world, and it no longer retries. The approver sees the reason in the console.
alter table approvals drop constraint approvals_status_check;
alter table approvals add constraint approvals_status_check
  check (status in ('pending','approved','rejected','executed','reversed','expired','failed'));
