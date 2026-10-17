import { DataTable, type DRow } from "@/components/datatable";
import { Section, Sparkline, StatTile } from "@/components/ui";
import { withProject, type Row } from "@/lib/db";
import { fmtCompact, fmtDay, fmtInt, num, str, truncate } from "@/lib/format";
import type { TabProps } from "./overview";

/** Competitor overview: DataForSEO Labs organic metrics for the site and each configured competitor, side by side. */
export async function DomainsTab({ projectId, project }: TabProps) {
  const d = await withProject(projectId, async (q) => {
    const run = (await q("select run_id, max(collected_at) as at from raw_domain_overview where project_id = $1 group by run_id order by max(collected_at) desc limit 1"))[0];
    if (!run) return { run: undefined, rows: [] as Row[], history: [] as Row[], pages: [] as Row[], authority: [] as Row[], noData: [] as Row[] };
    return {
      run,
      rows: await q(`select target, role, etv, keywords, pos_1, pos_2_3, pos_4_10, pos_11_20, pos_21_100, is_new, is_lost
        from raw_domain_overview where project_id = $1 and run_id = $2 order by role = 'own' desc, etv desc nulls last`, [run.run_id]),
      history: await q(`select target, month::text as month, etv from raw_domain_history where project_id = $1 and run_id = $2 order by target, month`, [run.run_id]),
      pages: await q(`select target, page, etv, keywords, pos_1, pos_2_3, pos_4_10 from raw_domain_pages where project_id = $1 and run_id = $2
        order by etv desc nulls last limit 400`, [run.run_id]),
      // authority from the latest backlink collection, which may be a different run
      authority: await q(`with lr as (select run_id from raw_backlink_summary where project_id = $1 order by collected_at desc limit 1)
        select target, rank, referring_domains from raw_backlink_summary where project_id = $1 and run_id = (select run_id from lr)`),
      noData: await q(`select affected_scope as target from collection_gaps where project_id = $1 and run_id = $2 and collector = 'competitor_overview'
        and reason like 'DataForSEO Labs has no organic data%'`, [run.run_id]),
    };
  });

  if (!d.run) {
    return (
      <div className="card">
        <strong>No competitor overview yet.</strong> The monthly run collects it for projects with configured competitors; run it now with{" "}
        <code>python worker.py run {str(project.slug)} competitor_overview &lt;key&gt;</code>.
      </div>
    );
  }

  const own = str((project.domains as string[])[0]).replace(/^www\./, "");
  const months = Array.from(new Set(d.history.map((h) => str(h.month)))).sort();
  const trend = new Map<string, (number | null)[]>();
  for (const t of new Set(d.history.map((h) => str(h.target)))) {
    const by = new Map(d.history.filter((h) => h.target === t).map((h) => [str(h.month), num(h.etv)]));
    trend.set(t, months.map((m) => by.get(m) ?? null));
  }
  const auth = new Map(d.authority.map((a) => [str(a.target), a]));
  const ownRow = d.rows.find((r) => r.role === "own");
  const comps = d.rows.filter((r) => r.role === "competitor");
  const leader = comps[0];

  const rows: Row[] = [...d.rows];
  if (!ownRow) rows.unshift({ target: own, role: "own" });   // shown as "no data" rather than left out
  for (const g of d.noData) if (!rows.some((r) => r.target === g.target)) rows.push({ target: g.target, role: "competitor" });

  const table: DRow[] = rows.map((r) => {
    const t = str(r.target);
    const a = auth.get(t);
    const you = r.role === "own";
    const hasData = r.etv != null || r.keywords != null;
    const top10 = hasData ? (num(r.pos_1) ?? 0) + (num(r.pos_2_3) ?? 0) + (num(r.pos_4_10) ?? 0) : null;
    return {
      key: t,
      facet: you ? "own" : "competitor",
      cells: [
        <span key="d">{you ? <strong>{t}</strong> : t}{you ? <span className="muted small"> (you)</span> : null}{!hasData ? <div className="muted small">no Labs data yet</div> : null}</span>,
        hasData ? fmtCompact(r.etv) : <span className="muted">–</span>,
        <span key="s" className="spark">{(trend.get(t) ?? []).some((v) => v != null) ? <Sparkline values={trend.get(t) ?? []} label={`Estimated organic traffic for ${t} over ${months.length} months`} /> : <span className="muted">–</span>}</span>,
        hasData ? fmtCompact(r.keywords) : <span className="muted">–</span>,
        hasData ? fmtInt(top10) : <span className="muted">–</span>,
        hasData ? fmtInt(r.pos_11_20) : <span className="muted">–</span>,
        hasData ? <span>{fmtInt(r.is_new)} <span className="muted">/</span> {fmtInt(r.is_lost)}</span> : <span className="muted">–</span>,
        fmtInt(a?.rank), fmtCompact(a?.referring_domains),
      ],
      values: [t, num(r.etv), null, num(r.keywords), top10, num(r.pos_11_20), num(r.is_new), num(a?.rank), num(a?.referring_domains)],
    };
  });

  const pageRows: DRow[] = d.pages.map((p, i) => ({
    key: `${str(p.target)}|${i}`,
    facet: str(p.target),
    cells: [
      <a key="p" href={str(p.page)} target="_blank" rel="noreferrer nofollow" className="wrap-anywhere">{truncate(str(p.page).replace(/^https?:\/\/(www\.)?/, ""), 90)}</a>,
      str(p.target), fmtCompact(p.etv), fmtCompact(p.keywords),
      fmtInt((num(p.pos_1) ?? 0) + (num(p.pos_2_3) ?? 0)), fmtInt(p.pos_4_10),
    ],
    values: [str(p.page), str(p.target), num(p.etv), num(p.keywords), (num(p.pos_1) ?? 0) + (num(p.pos_2_3) ?? 0), num(p.pos_4_10)],
  }));
  const pageTargets = Array.from(new Set(d.pages.map((p) => str(p.target))));

  return (
    <>
      <div className="kpis cols-4">
        <StatTile label="Your estimated organic traffic" value={ownRow ? fmtCompact(ownRow.etv) : "–"} foot={ownRow ? "visits a month, Google India" : "DataForSEO Labs has no data for the site yet"} />
        <StatTile label="Your ranking keywords" value={ownRow ? fmtCompact(ownRow.keywords) : "–"} foot={ownRow ? `${fmtInt((num(ownRow.pos_1) ?? 0) + (num(ownRow.pos_2_3) ?? 0) + (num(ownRow.pos_4_10) ?? 0))} in the top 10` : "it needs rankings to measure"} />
        <StatTile label="Competitors compared" value={fmtInt(comps.length)} foot={`collected ${fmtDay(d.run.at)}`} />
        <StatTile label="Largest by traffic" value={leader ? str(leader.target) : "–"} foot={leader ? `${fmtCompact(leader.etv)} visits a month` : ""} />
      </div>

      <Section title="Side by side" note="Estimated organic traffic and keywords are DataForSEO Labs estimates for Google India; the trend is the last 12 months of estimated traffic. New and lost are keywords that entered or left the rankings since the previous Labs update. Domain rank and referring domains come from the latest backlink collection.">
        <DataTable columns={[{ label: "Domain" }, { label: "Est. traffic / mo", num: true }, { label: "Trend", sort: false }, { label: "Keywords", num: true },
          { label: "Top 10", num: true }, { label: "11–20", num: true }, { label: "New / lost", num: true, title: "keywords new / lost since the previous update" },
          { label: "Domain rank", num: true }, { label: "Ref. domains", num: true }]}
          rows={table} filename="competitor-overview" searchLabel="Filter domains" empty="No domains collected." />
      </Section>

      <Section title="Top pages" note="Each domain's pages with the most estimated organic traffic: what earns your competitors their visits.">
        <DataTable columns={[{ label: "Page", sort: false }, { label: "Domain" }, { label: "Est. traffic / mo", num: true }, { label: "Keywords", num: true },
          { label: "Top 3", num: true }, { label: "4–10", num: true }]}
          rows={pageRows} facets={pageTargets.map((t) => ({ key: t, label: t }))} defaultFacet={pageTargets.includes(own) ? own : pageTargets[0]}
          filename="competitor-top-pages" searchLabel="Filter pages" pageSize={20} empty="No top pages collected." />
      </Section>
    </>
  );
}
