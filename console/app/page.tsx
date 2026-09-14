import Link from "next/link";
import { listProjects, withProject } from "@/lib/db";
import { fmtDate, fmtUsd, str } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Home() {
  const projects = await listProjects();
  const rows = await Promise.all(
    projects.map(async (p) => {
      const id = str(p.id);
      return withProject(id, async (q) => {
        const [pending] = await q("select count(*)::int as n from approvals where project_id = $1 and status = 'pending'");
        const [critical] = await q("select count(*)::int as n from approvals where project_id = $1 and status = 'pending' and severity = 'critical'");
        const [spend] = await q("select coalesce(sum(cost_usd),0) as usd from runs where project_id = $1 and started_at >= date_trunc('month', now())");
        const [last] = await q("select workflow, status, started_at from runs where project_id = $1 order by started_at desc limit 1");
        const [openIssues] = await q("select count(*)::int as n from issues where project_id = $1 and status = 'open' and severity in ('critical','high')");
        return { p, pending: pending?.n, critical: critical?.n, spend: spend?.usd, last, openIssues: openIssues?.n };
      });
    }),
  );
  return (
    <>
      <h1>Projects</h1>
      <table>
        <thead><tr><th>Project</th><th>Vertical</th><th>Pending approvals</th><th>Critical / high issues</th><th>Spend this month</th><th>Last run</th></tr></thead>
        <tbody>
          {rows.map(({ p, pending, critical, spend, last, openIssues }) => (
            <tr key={str(p.id)}>
              <td><Link href={`/projects/${str(p.slug)}`}>{str(p.display_name)}</Link> {p.halted_reason ? <span className="pill critical">halted</span> : null}</td>
              <td>{str(p.vertical)}</td>
              <td>{String(pending)} {Number(critical) > 0 && <span className="pill critical">{String(critical)} critical</span>}</td>
              <td>{String(openIssues)}</td>
              <td>{fmtUsd(spend)} / {fmtUsd(p.monthly_cost_cap_usd)}</td>
              <td>{last ? `${str(last.workflow)} (${str(last.status)}) ${fmtDate(last.started_at)}` : "never"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
