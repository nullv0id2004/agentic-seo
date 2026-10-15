import type { ReactNode } from "react";
import { Sidebar, type TabKey } from "@/components/ui";
import { listProjects, withProject, type Row } from "@/lib/db";
import { str } from "@/lib/format";

/**
 * Every project page: sidebar on the left, a header naming the project and the page, then the page itself.
 * `actions` sits at the right of the header (range chips, a button); `group` and `title` name the page.
 */
export async function ProjectShell({ project, active, range = 28, group, title, actions, children }: {
  project: Row; active: TabKey | "approvals" | "reports"; range?: number; group: string; title: string; actions?: ReactNode; children: ReactNode;
}) {
  const slug = str(project.slug);
  const [projects, pending] = await Promise.all([
    listProjects(),
    withProject(str(project.id), (q) => q("select count(*)::int as n from approvals where project_id = $1 and status = 'pending'")),
  ]);
  const others = projects.filter((p) => p.slug !== slug).map((p) => ({ slug: str(p.slug), name: str(p.display_name) }));
  return (
    <div className="app">
      <Sidebar slug={slug} active={active} range={range} name={str(project.display_name)} others={others} pending={Number(pending[0]?.n ?? 0)} />
      <div className="content">
        <div className="page-head">
          <div>
            <div className="crumbs">{str(project.display_name)} · {(project.domains as string[]).join(", ")} · {group}</div>
            <h1>{title}</h1>
          </div>
          {actions ? <div className="actions">{actions}</div> : null}
        </div>
        {project.halted_reason ? (
          <div className="card alert"><strong>Halted.</strong> {str(project.halted_reason)} All workflows except the probes are skipped until a probe comes back clean.</div>
        ) : null}
        {children}
      </div>
    </div>
  );
}
