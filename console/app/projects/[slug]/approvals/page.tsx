import { notFound } from "next/navigation";
import { projectBySlug, withProject } from "@/lib/db";
import { fmtDate, str } from "@/lib/format";
import { decideApproval } from "./actions";

export const dynamic = "force-dynamic";

export default async function ApprovalsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const project = await projectBySlug(slug);
  if (!project) notFound();
  const canDecide = String(project.approver_id) === process.env.CONSOLE_APPROVER_ID;
  const data = await withProject(str(project.id), async (q) => ({
    pending: await q("select * from approvals where project_id = $1 and status = 'pending' order by (severity = 'critical') desc, created_at"),
    decided: await q("select id, action_type, status, summary_plain_english, decided_at, executed_at, reversed_at from approvals where project_id = $1 and status <> 'pending' order by coalesce(decided_at, created_at) desc limit 30"),
  }));
  return (
    <>
      <h1>Approvals <span className="muted">{str(project.display_name)}</span></h1>
      {!canDecide && <p className="pill critical">This console is not the approver for this project. Read only.</p>}
      <h2>Pending ({data.pending.length})</h2>
      {data.pending.map((a) => (
        <div className="card" key={str(a.id)}>
          <div className="row">
            <span className={`pill ${str(a.severity) === "critical" ? "critical" : ""}`}>{str(a.severity)}</span>
            <strong>{str(a.action_type)}</strong>
            <span className="muted">requested by {str(a.requested_by_agent)} · {fmtDate(a.created_at)}</span>
          </div>
          <p>{str(a.summary_plain_english)}</p>
          <details>
            <summary>Payload and reversal</summary>
            <pre>{JSON.stringify(a.payload, null, 1)}</pre>
            <p className="muted">Reversal: <code>{JSON.stringify(a.reversal_payload)}</code></p>
          </details>
          {canDecide && (
            <form action={decideApproval} className="row">
              <input type="hidden" name="slug" value={slug} />
              <input type="hidden" name="approval_id" value={str(a.id)} />
              <button className="primary" name="decision" value="approve" type="submit">Approve</button>
              <button className="danger" name="decision" value="reject" type="submit">Reject</button>
            </form>
          )}
        </div>
      ))}
      {data.pending.length === 0 && <p className="muted">Nothing pending.</p>}
      <h2>Recently decided</h2>
      <table>
        <thead><tr><th>Action</th><th>Status</th><th>Summary</th><th>Decided</th><th>Executed</th><th>Reversed</th></tr></thead>
        <tbody>
          {data.decided.map((a) => (
            <tr key={str(a.id)}><td>{str(a.action_type)}</td><td>{str(a.status)}</td><td>{str(a.summary_plain_english)}</td><td>{fmtDate(a.decided_at)}</td><td>{fmtDate(a.executed_at)}</td><td>{fmtDate(a.reversed_at)}</td></tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
