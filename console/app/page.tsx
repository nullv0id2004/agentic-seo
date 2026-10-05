import Link from "next/link";
import { Empty } from "@/components/ui";
import { listProjects, withProject } from "@/lib/db";
import { delta, fmtCompact, fmtDate, fmtInt, fmtUsd, str } from "@/lib/format";
import { GA4_TOTALS, GSC_TOTALS } from "@/lib/sql";

export const dynamic = "force-dynamic";

function Change({ cur, prev, days }: { cur: unknown; prev: unknown; days: unknown }) {
  const d = Number(days) > 0 ? delta(cur, prev) : null;
  if (!d || d.dir === "flat") return null;
  return <span className={`delta small ${d.dir === "up" ? "up-good" : "down-bad"}`}> {d.dir === "up" ? "▲" : "▼"} {d.text}</span>;
}

export default async function Home() {
  const projects = await listProjects();
  const rows = await Promise.all(
    projects.map(async (p) => {
      const id = str(p.id);
      return withProject(id, async (q) => {
        const [approvals] = await q("select count(*)::int as n, count(*) filter (where severity = 'critical')::int as critical from approvals where project_id = $1 and status = 'pending'");
        const [spend] = await q("select coalesce(sum(cost_usd),0) as usd from runs where project_id = $1 and started_at >= date_trunc('month', now())");
        const [last] = await q("select workflow, status, started_at from runs where project_id = $1 order by started_at desc limit 1");
        const [issues] = await q("select count(*) filter (where severity = 'critical')::int as critical, count(*) filter (where severity = 'high')::int as high from issues where project_id = $1 and status = 'open'");
        const [gsc] = await q(GSC_TOTALS, [28]);
        const [ga] = await q(GA4_TOTALS, [28]);
        const [ai] = await q(`select count(*)::int as asked, count(*) filter (where cites_project)::int as citing from raw_llm_responses
          where project_id = $1 and run_id = (select run_id from raw_llm_responses where project_id = $1 order by collected_at desc limit 1)`);
        return { p, approvals, spend: spend?.usd, last, issues, gsc: gsc ?? {}, ga: ga ?? {}, ai };
      });
    }),
  );
  return (
    <>
      <h1>Projects</h1>
      <p className="muted small">Search and traffic figures cover the last 28 collected days, compared with the 28 days before.</p>
      <div className="scroll"><table>
        <thead><tr>
          <th>Project</th><th className="num">Search clicks</th><th className="num">Impressions</th><th className="num">Sessions</th>
          <th>AI citations</th><th>Pending approvals</th><th>Critical / high issues</th><th>Spend this month</th><th>Last run</th>
        </tr></thead>
        <tbody>
          {rows.map(({ p, approvals, spend, last, issues, gsc, ga, ai }) => (
            <tr key={str(p.id)}>
              <td><Link href={`/projects/${str(p.slug)}`}><strong>{str(p.display_name)}</strong></Link> {p.halted_reason ? <span className="pill critical">halted</span> : null}
                <div className="muted small">{str(p.vertical)}</div></td>
              <td className="num">{fmtCompact(gsc.clicks)}<Change cur={gsc.clicks} prev={gsc.prev_clicks} days={gsc.prev_days} /></td>
              <td className="num">{fmtCompact(gsc.impressions)}<Change cur={gsc.impressions} prev={gsc.prev_impressions} days={gsc.prev_days} /></td>
              <td className="num">{fmtCompact(ga.sessions)}<Change cur={ga.sessions} prev={ga.prev_sessions} days={ga.prev_days} /></td>
              <td>{ai?.asked ? `${fmtInt(ai.citing)} of ${fmtInt(ai.asked)} prompts` : <span className="muted">not checked</span>}</td>
              <td><Link href={`/projects/${str(p.slug)}/approvals`}>{fmtInt(approvals?.n)}</Link> {Number(approvals?.critical) > 0 && <span className="pill critical">{fmtInt(approvals?.critical)} critical</span>}</td>
              <td>{fmtInt(issues?.critical)} / {fmtInt(issues?.high)}</td>
              <td className="nowrap">{fmtUsd(spend)} / {fmtUsd(p.monthly_cost_cap_usd, 0)}</td>
              <td className="small">{last ? <>{str(last.workflow).replaceAll("_", " ")} <span className="muted">({str(last.status)})</span><div className="muted">{fmtDate(last.started_at)}</div></> : "never"}</td>
            </tr>
          ))}
          {rows.length === 0 && <Empty cols={9}>No active projects.</Empty>}
        </tbody>
      </table></div>
    </>
  );
}
