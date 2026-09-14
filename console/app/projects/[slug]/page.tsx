import Link from "next/link";
import { notFound } from "next/navigation";
import { projectBySlug, withProject } from "@/lib/db";
import { fmtDate, fmtUsd, str } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function ProjectPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const project = await projectBySlug(slug);
  if (!project) notFound();
  const id = str(project.id);
  const data = await withProject(id, async (q) => ({
    runs: await q("select id, workflow, trigger, status, started_at, ended_at, cost_usd, halt_reason from runs where project_id = $1 order by started_at desc limit 20"),
    issues: await q("select id, severity, issue_type, url, evidence, recommended_fix, created_at from issues where project_id = $1 and status = 'open' order by array_position(array['critical','high','medium','low'], severity), created_at desc limit 100"),
    gaps: await q("select collector, reason, affected_scope, created_at from collection_gaps where project_id = $1 order by created_at desc limit 30"),
    reports: await q("select id, period, created_at, caveats from reports where project_id = $1 order by created_at desc limit 12"),
    trends: await q("select kind, name, source_url, observed_on, detail from trend_events where project_id = $1 order by observed_on desc limit 20"),
    pending: await q("select count(*)::int as n from approvals where project_id = $1 and status = 'pending'"),
  }));
  return (
    <>
      <h1>{str(project.display_name)} <span className="muted">({slug}, {str(project.vertical)})</span></h1>
      <p>
        <Link href={`/projects/${slug}/approvals`}>Approvals queue: {String(data.pending[0]?.n ?? 0)} pending</Link>
        {" · "}enabled agents: {(project.enabled_agents as string[]).join(", ")}
        {" · "}cap {fmtUsd(project.monthly_cost_cap_usd)}/month
      </p>

      <h2>Open issues</h2>
      <table>
        <thead><tr><th>Severity</th><th>Type</th><th>URL</th><th>Evidence</th><th>Recommended fix</th></tr></thead>
        <tbody>
          {data.issues.map((i) => (
            <tr key={str(i.id)}>
              <td><span className={`pill ${str(i.severity)}`}>{str(i.severity)}</span></td>
              <td>{str(i.issue_type)}</td>
              <td>{i.url ? str(i.url) : <em className="muted">protected route (see raw row)</em>}</td>
              <td>{str(i.evidence)}</td>
              <td>{str(i.recommended_fix)}</td>
            </tr>
          ))}
          {data.issues.length === 0 && <tr><td colSpan={5} className="muted">none</td></tr>}
        </tbody>
      </table>

      <h2>Runs</h2>
      <table>
        <thead><tr><th>Workflow</th><th>Trigger</th><th>Status</th><th>Started</th><th>Ended</th><th>Cost</th><th>Note</th></tr></thead>
        <tbody>
          {data.runs.map((r) => (
            <tr key={str(r.id)}>
              <td>{str(r.workflow)}</td><td>{str(r.trigger)}</td>
              <td><span className={`pill ${str(r.status) === "done" ? "ok" : str(r.status).startsWith("halted") || str(r.status) === "failed" ? "critical" : ""}`}>{str(r.status)}</span></td>
              <td>{fmtDate(r.started_at)}</td><td>{fmtDate(r.ended_at)}</td><td>{fmtUsd(r.cost_usd)}</td><td className="muted">{str(r.halt_reason)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Collection gaps</h2>
      <table>
        <thead><tr><th>Collector</th><th>Reason</th><th>Scope</th><th>When</th></tr></thead>
        <tbody>
          {data.gaps.map((g, i) => (
            <tr key={i}><td>{str(g.collector)}</td><td>{str(g.reason)}</td><td>{str(g.affected_scope)}</td><td>{fmtDate(g.created_at)}</td></tr>
          ))}
          {data.gaps.length === 0 && <tr><td colSpan={4} className="muted">none</td></tr>}
        </tbody>
      </table>

      <h2>Reports</h2>
      <ul>
        {data.reports.map((r) => (
          <li key={str(r.id)}>
            <Link href={`/projects/${slug}/reports/${str(r.id)}`}>{str(r.period)}</Link> <span className="muted">{fmtDate(r.created_at)}</span>
            {Array.isArray(r.caveats) && r.caveats.length > 0 && <span className="pill high"> {r.caveats.length} caveat(s)</span>}
          </li>
        ))}
      </ul>

      <h2>Monitoring events</h2>
      <table>
        <thead><tr><th>Date</th><th>Kind</th><th>Event</th><th>Detail</th></tr></thead>
        <tbody>
          {data.trends.map((t, i) => (
            <tr key={i}><td>{str(t.observed_on).slice(0, 10)}</td><td>{str(t.kind)}</td><td><a href={str(t.source_url)} rel="noreferrer" target="_blank">{str(t.name)}</a></td><td>{str(t.detail)}</td></tr>
          ))}
          {data.trends.length === 0 && <tr><td colSpan={4} className="muted">none</td></tr>}
        </tbody>
      </table>
    </>
  );
}
