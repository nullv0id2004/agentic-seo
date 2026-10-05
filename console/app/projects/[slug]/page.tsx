import Link from "next/link";
import { notFound } from "next/navigation";
import { AiTab } from "@/components/project/ai";
import { ChangesTab } from "@/components/project/changes";
import { ContentTab } from "@/components/project/content";
import { KeywordsTab } from "@/components/project/keywords";
import { OperationsTab } from "@/components/project/operations";
import { OverviewTab, type TabProps } from "@/components/project/overview";
import { RankingsTab } from "@/components/project/rankings";
import { SearchTab } from "@/components/project/search";
import { TechnicalTab } from "@/components/project/technical";
import { TrafficTab } from "@/components/project/traffic";
import { RANGES, RangeFilter, TABS, Tabs, type TabKey } from "@/components/ui";
import { projectBySlug, withProject } from "@/lib/db";
import { fmtUsd, str } from "@/lib/format";

export const dynamic = "force-dynamic";

const RENDER: Record<TabKey, (p: TabProps) => Promise<React.ReactElement>> = {
  overview: OverviewTab, changes: ChangesTab, search: SearchTab, traffic: TrafficTab, keywords: KeywordsTab, rankings: RankingsTab,
  technical: TechnicalTab, ai: AiTab, content: ContentTab, operations: OperationsTab,
};

// Tabs whose numbers do not depend on the range hide the filter rather than offer a control that does nothing.
const RANGED: TabKey[] = ["overview", "search", "traffic", "keywords", "rankings", "ai", "operations"];

export default async function ProjectPage({ params, searchParams }: {
  params: Promise<{ slug: string }>; searchParams: Promise<{ tab?: string; range?: string }>;
}) {
  const { slug } = await params;
  const sp = await searchParams;
  const project = await projectBySlug(slug);
  if (!project) notFound();
  const tab = (TABS.some(([k]) => k === sp.tab) ? sp.tab : "overview") as TabKey;
  const range = RANGES.includes(Number(sp.range) as (typeof RANGES)[number]) ? Number(sp.range) : 28;
  const id = str(project.id);
  const pending = await withProject(id, (q) => q("select count(*)::int as n from approvals where project_id = $1 and status = 'pending'"));
  const Body = RENDER[tab];

  return (
    <>
      <h1>{str(project.display_name)} <span className="muted small">{(project.domains as string[]).join(", ")} · {str(project.vertical)}</span></h1>
      {project.halted_reason ? (
        <div className="card alert"><strong>Halted.</strong> {str(project.halted_reason)} All workflows except the probes are skipped until a probe comes back clean.</div>
      ) : null}
      <p className="secondary small">
        <Link href={`/projects/${slug}/approvals`}>Approvals queue: {String(pending[0]?.n ?? 0)} pending</Link>
        {" · "}<Link href={`/projects/${slug}/reports`}>Reports</Link>
        {" · "}cap {fmtUsd(project.monthly_cost_cap_usd, 0)}/month
      </p>
      <Tabs slug={slug} active={tab} range={range} />
      {RANGED.includes(tab) ? <RangeFilter slug={slug} tab={tab} range={range} /> : <div style={{ height: 12 }} />}
      <Body projectId={id} slug={slug} range={range} project={project} />
    </>
  );
}
