import { notFound } from "next/navigation";
import { projectBySlug, withProject } from "@/lib/db";
import { fmtDate, str } from "@/lib/format";

export const dynamic = "force-dynamic";

type Metric = { value: number | null; prior_value?: number | null; derived_from_rows?: number; period?: string };

export default async function ReportPage({ params }: { params: Promise<{ slug: string; id: string }> }) {
  const { slug, id } = await params;
  const project = await projectBySlug(slug);
  if (!project) notFound();
  const [report] = await withProject(str(project.id), (q) => q("select * from reports where project_id = $1 and id = $2", [id]));
  if (!report) notFound();
  const metrics = (report.metrics ?? {}) as Record<string, Metric | string>;
  const caveats = (report.caveats ?? []) as { collector: string; reason: string; affected_scope?: string }[];
  return (
    <>
      <h1>Report {str(report.period)} <span className="muted">{str(project.display_name)}</span></h1>
      <p className="muted">Generated {fmtDate(report.created_at)}. Data cutoff {str(metrics.cutoff_date)}.</p>
      {caveats.length > 0 && (
        <div className="card">
          <strong>Caveats: data gaps in this period.</strong> Affected metrics are shown as unavailable, not estimated.
          <ul>{caveats.map((c, i) => <li key={i}><code>{c.collector}</code>: {c.reason} {c.affected_scope && <span className="muted">({c.affected_scope})</span>}</li>)}</ul>
        </div>
      )}
      <h2>Metrics</h2>
      <table>
        <thead><tr><th>Metric</th><th>Value</th><th>Prior period</th><th>Rows</th></tr></thead>
        <tbody>
          {Object.entries(metrics).filter(([k]) => k !== "cutoff_date").map(([name, m]) => {
            const mm = m as Metric;
            return (
              <tr key={name}>
                <td>{name}</td>
                <td>{mm.value == null ? <em className="muted">unavailable</em> : String(mm.value)}</td>
                <td>{mm.prior_value == null ? "" : String(mm.prior_value)}</td>
                <td className="muted">{mm.derived_from_rows ?? ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <h2>Commentary</h2>
      <p>{str(report.commentary)}</p>
    </>
  );
}
