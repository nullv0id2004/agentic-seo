import Link from "next/link";
import { Empty, Section, StatTile, Status } from "@/components/ui";
import { withProject, type Row } from "@/lib/db";
import { fmtDate, fmtDay, fmtInt, pathOf, str, truncate } from "@/lib/format";
import type { TabProps } from "./overview";

// Where each action type acts. Only the GitHub actions touch the site's code, and only through a pull
// request that a person merges; nothing here deploys anything.
const WHERE: Record<string, string> = {
  open_fix_pr: "Pull request in the site's repository", robots_change: "Pull request in the site's repository",
  sitemap_change: "Pull request in the site's repository", canonical_change: "Pull request in the site's repository",
  hreflang_change: "Pull request in the site's repository", keyword_mapping: "SEO database only (keyword to page map)",
  page_owner: "Acknowledgement only", publish_content: "Publishes to the CMS", send_pitch: "Sends one email",
  spend_above_cap: "Budget decision only",
};

type FileChange = { path: string; content?: string };
type Prior = { path: string; content: string | null };
type Mapping = { keyword: string; mapped_url: string | null };

function Outcome({ a }: { a: Row }) {
  const res = (a.execution_result ?? {}) as { ok?: boolean; error?: string; attempts?: number; detail?: Record<string, unknown> };
  const detail = res.detail ?? {};
  const payload = (a.payload ?? {}) as Record<string, unknown>;
  const rev = (a.reversal_payload ?? {}) as Record<string, unknown>;
  const type = str(a.action_type);

  if (a.status === "failed" || (a.status === "approved" && res.ok === false)) {
    return <div className="small"><strong>Not done.</strong> {str(res.error)}{res.attempts ? <span className="muted"> ({res.attempts} attempt{res.attempts > 1 ? "s" : ""})</span> : null}. Nothing changed.</div>;
  }
  if (a.status === "rejected") return <div className="small muted">Rejected. Nothing changed.</div>;
  if (a.status === "approved") return <div className="small muted">Approved, waiting for the worker (it acts within a minute).</div>;

  if (type in WHERE && WHERE[type].startsWith("Pull request")) {
    const files = (payload.file_changes as FileChange[] | undefined) ?? [];
    const prior = (rev.prior_files as Prior[] | undefined) ?? [];
    return (
      <div className="small">
        {detail.url ? <div><a href={str(detail.url)} target="_blank" rel="noreferrer">Pull request #{str(detail.pr_number)}</a> on branch <code>{str(detail.branch)}</code></div> : null}
        {files.length === 0
          ? <div className="muted">The pull request holds a fix note for a developer or coding agent. The site changes only when someone implements it in that PR, merges it and deploys.</div>
          : <div className="muted">The pull request edits {files.length} file(s). It reaches the site only after it is merged and deployed.</div>}
        {(files.length > 0 ? files : prior).map((f, i) => {
          const before = prior.find((p) => p.path === f.path)?.content;
          const after = (f as FileChange).content;
          return (
            <details key={i}>
              <summary><code>{f.path}</code> {before == null ? "new file" : "changed"}</summary>
              {before != null ? <><div className="muted">Before</div><pre>{truncate(before, 3000)}</pre></> : null}
              {after != null ? <><div className="muted">After</div><pre>{truncate(after, 3000)}</pre></> : null}
            </details>
          );
        })}
        {a.status === "reversed" ? <div>Reversed {fmtDate(a.reversed_at)}: pull request closed and branch deleted.</div> : null}
      </div>
    );
  }
  if (type === "keyword_mapping") {
    const after = (payload.mappings as Mapping[] | undefined) ?? [];
    const before = (rev.previous as Mapping[] | undefined) ?? [];
    return (
      <div className="small">
        <table className="small">
          <thead><tr><th>Keyword</th><th>Before</th><th>After</th></tr></thead>
          <tbody>
            {after.map((m) => (
              <tr key={m.keyword}><td>{m.keyword}</td>
                <td className="muted">{before.find((b) => b.keyword === m.keyword)?.mapped_url ? pathOf(before.find((b) => b.keyword === m.keyword)?.mapped_url) : "unmapped"}</td>
                <td>{m.mapped_url ? pathOf(m.mapped_url) : "unmapped"}</td></tr>
            ))}
          </tbody>
        </table>
        {a.status === "reversed" ? <div>Reversed {fmtDate(a.reversed_at)}: previous mapping restored.</div> : null}
      </div>
    );
  }
  if (type === "send_pitch") return <div className="small">Sent to {str(detail.to)}.{a.status === "reversed" ? " A retraction was sent." : ""}</div>;
  if (type === "publish_content") return <div className="small">Published at <a href={str(detail.published_url)} target="_blank" rel="noreferrer">{pathOf(detail.published_url)}</a>.{a.status === "reversed" ? " Unpublished." : ""}</div>;
  return <div className="small muted">{truncate(JSON.stringify(detail), 200)}</div>;
}

function Verification({ a }: { a: Row }) {
  if (!a.issue_status) return <span className="muted small">{WHERE[str(a.action_type)]?.startsWith("Pull request") ? "no linked issue" : "not applicable"}</span>;
  if (a.issue_status === "resolved") {
    return <div className="small"><span className="pill ok">fixed on the site</span><div className="muted">a site audit on {fmtDay(a.issue_resolved_at)} no longer found it</div></div>;
  }
  return (
    <div className="small">
      <span className="pill warning">still on the site</span>
      <div className="muted">last seen {fmtDay(a.issue_last_seen_at)}{Number(a.audits_since) > 0 ? `, ${a.audits_since} audit(s) since approval` : ", no audit since approval"}</div>
    </div>
  );
}

export async function ChangesTab({ projectId, slug }: TabProps) {
  const rows = await withProject(projectId, (q) => q(`
    select a.id, a.action_type, a.summary_plain_english, a.status, a.severity, a.requested_by_agent, a.payload, a.reversal_payload,
      a.execution_result, a.created_at, a.decided_at, a.executed_at, a.reversed_at,
      i.status as issue_status, i.resolved_at as issue_resolved_at, i.last_seen_at as issue_last_seen_at, i.url as issue_url,
      (select count(*)::int from runs r where r.project_id = a.project_id and r.workflow in ('post_deploy_audit', 'monthly_full')
         and r.started_at > coalesce(a.executed_at, a.decided_at)) as audits_since
    from approvals a
    left join issues i on i.project_id = a.project_id and i.fingerprint = a.payload->>'issue_fingerprint'
    where a.project_id = $1 and a.status <> 'pending'
    order by coalesce(a.executed_at, a.decided_at, a.created_at) desc limit 200`));

  const executed = rows.filter((r) => r.status === "executed" || r.status === "reversed");
  const prs = executed.filter((r) => WHERE[str(r.action_type)]?.startsWith("Pull request"));
  const fixed = rows.filter((r) => r.issue_status === "resolved").length;
  const failed = rows.filter((r) => r.status === "failed" || (r.status === "approved" && (r.execution_result as { ok?: boolean } | null)?.ok === false));

  return (
    <>
      <div className="kpis">
        <StatTile label="Changes carried out" value={fmtInt(executed.length)} foot={`${fmtInt(rows.length)} decided in total`} />
        <StatTile label="Pull requests opened" value={fmtInt(prs.length)} foot="the worker never merges or deploys" />
        <StatTile label="Fixes confirmed on the site" value={fmtInt(fixed)} foot="a later audit no longer finds the issue" />
        <StatTile label="Approved but not done" value={fmtInt(failed.length)} foot="failed actions change nothing" />
      </div>

      <div className="card">
        <strong>How an approval reaches the site.</strong>
        <ol className="small">
          <li>You approve in the <Link href={`/projects/${slug}/approvals`}>approvals queue</Link>. Within a minute the worker acts: a fix opens a pull request in the site&apos;s repository. It never merges, never pushes to the main branch and never deploys.</li>
          <li>A developer, or a coding agent, implements the change in that pull request, reviews it and merges it.</li>
          <li>The site&apos;s own release pipeline deploys it: preprod first, then production.</li>
          <li>The production deploy calls the worker, which audits the site. If the issue is gone, it is marked resolved here as fixed on the site.</li>
        </ol>
        <p className="small muted">Keyword mappings change only the SEO database. Pitches send one email. Every executed change can be reversed; the reversal is stored with it.</p>
      </div>

      <Section title="Change log" note="Newest first. Open a row to see what changed from what.">
        <div className="scroll"><table>
          <thead><tr><th>Change</th><th>Acts on</th><th>Status</th><th>What happened</th><th>Verified on the site</th><th>When</th></tr></thead>
          <tbody>
            {rows.map((a) => (
              <tr key={str(a.id)}>
                <td>
                  <strong>{str(a.action_type).replaceAll("_", " ")}</strong> {a.severity === "critical" ? <span className="pill critical">critical</span> : null}
                  <div className="small">{truncate(a.summary_plain_english, 220)}</div>
                  {a.issue_url ? <div className="muted small wrap-anywhere">{pathOf(a.issue_url)}</div> : null}
                </td>
                <td className="small">{WHERE[str(a.action_type)] ?? str(a.action_type)}</td>
                <td><Status value={a.status === "approved" && (a.execution_result as { ok?: boolean } | null)?.ok === false ? "failed" : a.status} /></td>
                <td><Outcome a={a} /></td>
                <td><Verification a={a} /></td>
                <td className="small nowrap">
                  <div>requested {fmtDay(a.created_at)}</div>
                  {a.decided_at ? <div>decided {fmtDay(a.decided_at)}</div> : null}
                  {a.executed_at ? <div>done {fmtDate(a.executed_at)}</div> : null}
                  {a.reversed_at ? <div>reversed {fmtDay(a.reversed_at)}</div> : null}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <Empty cols={6}>No decisions yet.</Empty>}
          </tbody>
        </table></div>
      </Section>
    </>
  );
}
