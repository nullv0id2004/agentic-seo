import { notFound } from "next/navigation";
import { AiTab } from "@/components/project/ai";
import { ChangesTab } from "@/components/project/changes";
import { CompetitorsTab } from "@/components/project/competitors";
import { ContentTab } from "@/components/project/content";
import { KeywordsTab } from "@/components/project/keywords";
import { LinksTab } from "@/components/project/links";
import { OperationsTab } from "@/components/project/operations";
import { OverviewTab, type TabProps } from "@/components/project/overview";
import { RankingsTab } from "@/components/project/rankings";
import { SearchTab } from "@/components/project/search";
import { TechnicalTab } from "@/components/project/technical";
import { TrafficTab } from "@/components/project/traffic";
import { ProjectShell } from "@/components/shell";
import { RANGES, RangeFilter, TABS, groupOf, tabLabel, type TabKey } from "@/components/ui";
import { projectBySlug } from "@/lib/db";
import { str } from "@/lib/format";

export const dynamic = "force-dynamic";

const RENDER: Record<TabKey, (p: TabProps) => Promise<React.ReactElement>> = {
  overview: OverviewTab, changes: ChangesTab, search: SearchTab, traffic: TrafficTab, keywords: KeywordsTab, rankings: RankingsTab,
  technical: TechnicalTab, ai: AiTab, content: ContentTab, operations: OperationsTab, competitors: CompetitorsTab, links: LinksTab,
};

// Pages whose numbers do not depend on the range hide the filter rather than offer a control that does nothing.
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
  const Body = RENDER[tab];

  return (
    <ProjectShell project={project} active={tab} range={range} group={groupOf(tab)} title={tabLabel(tab)}
      actions={RANGED.includes(tab) ? <RangeFilter slug={slug} tab={tab} range={range} /> : undefined}>
      <Body projectId={str(project.id)} slug={slug} range={range} project={project} />
    </ProjectShell>
  );
}
