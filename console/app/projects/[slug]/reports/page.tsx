import Link from "next/link";
import { notFound } from "next/navigation";
import { Empty } from "@/components/ui";
import { projectBySlug, withProject } from "@/lib/db";
import { fmtDate, str, truncate } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function ReportsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const project = await projectBySlug(slug);
  if (!project) notFound();
  const reports = await withProject(str(project.id), (q) =>
    q("select id, period, created_at, caveats, commentary from reports where project_id = $1 order by created_at desc limit 100"));
  return (
    <>
      <p className="small"><Link href={`/projects/${slug}`}>← {str(project.display_name)}</Link></p>
      <h1>Reports</h1>
      <p className="muted small">Weekly and monthly reports. Every number in a report comes from raw rows; caveats list the data that was missing.</p>
      <div className="scroll"><table>
        <thead><tr><th>Period</th><th>Summary</th><th>Caveats</th><th>Generated</th></tr></thead>
        <tbody>
          {reports.map((r) => (
            <tr key={str(r.id)}>
              <td className="nowrap"><Link href={`/projects/${slug}/reports/${str(r.id)}`}>{str(r.period)}</Link></td>
              <td className="small">{truncate(r.commentary, 220)}</td>
              <td>{Array.isArray(r.caveats) && r.caveats.length > 0 ? <span className="pill warning">{r.caveats.length}</span> : <span className="muted">none</span>}</td>
              <td className="nowrap muted">{fmtDate(r.created_at)}</td>
            </tr>
          ))}
          {reports.length === 0 && <Empty cols={4}>No reports yet.</Empty>}
        </tbody>
      </table></div>
    </>
  );
}
