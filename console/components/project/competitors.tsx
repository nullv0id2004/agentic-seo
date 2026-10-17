import { Difficulty, Intent, Position } from "@/components/badges";
import { DataTable, type DRow } from "@/components/datatable";
import { Section, StatTile } from "@/components/ui";
import { withProject } from "@/lib/db";
import { fmtDay, fmtInt, fmtUsd, num, pathOf, str } from "@/lib/format";
import { GENERIC, registrable } from "@/lib/domains";
import { projectDomains } from "@/lib/sql";
import type { TabProps } from "./overview";

type Gap = "missing" | "untapped" | "weak" | "strong";
const GAP_LABEL: Record<Gap, string> = { missing: "Missing", untapped: "Untapped", weak: "Weak", strong: "Strong" };
const GAP_HELP: Record<Gap, string> = {
  missing: "two or more competitors rank, you don't",
  untapped: "one competitor ranks, you don't",
  weak: "you rank, but below the best competitor",
  strong: "you rank at or above every competitor",
};

export async function CompetitorsTab({ projectId, project }: TabProps) {
  const own = projectDomains(project);
  const competitors = ((project.competitors as string[] | null) ?? []).map(registrable);
  const excluded = Array.from(new Set([...own, ...own.map(registrable), ...competitors, ...GENERIC]));

  const d = await withProject(projectId, async (q) => ({
    keywords: await q(`with lr as (select run_id from raw_keyword_ideas where project_id = $1 and source = 'competitor' order by collected_at desc limit 1),
      comp as (select keyword, competitor, min(competitor_rank) as rank, max(search_volume) as volume, max(keyword_difficulty) as kd, max(cpc) as cpc,
                 (array_agg(intent) filter (where intent is not null))[1] as intent, (array_agg(competitor_url order by competitor_rank))[1] as url
               from raw_keyword_ideas where project_id = $1 and run_id = (select run_id from lr) and source = 'competitor' group by keyword, competitor),
      orun as (select run_id from raw_keyword_ideas where project_id = $1 and source = 'own' order by collected_at desc limit 1),
      own as (select keyword, min(competitor_rank) as rank, (array_agg(competitor_url order by competitor_rank))[1] as url
              from raw_keyword_ideas where project_id = $1 and run_id = (select run_id from orun) and source = 'own' group by keyword),
      gq as (select lower(query) as k, sum(position * impressions) / nullif(sum(impressions), 0) as pos
             from raw_gsc_performance where project_id = $1 and query is not null and date >= current_date - 90 group by lower(query))
      select c.keyword, max(c.volume) as volume, max(c.kd) as kd, max(c.cpc) as cpc,
        (array_agg(c.intent) filter (where c.intent is not null))[1] as intent,
        jsonb_object_agg(c.competitor, jsonb_build_object('r', c.rank, 'u', c.url)) as positions,
        count(*)::int as ranking, min(c.rank) as best, max(o.rank) as own_rank, max(o.url) as own_url, max(gq.pos) as gsc_pos
      from comp c left join own o on o.keyword = c.keyword left join gq on gq.k = c.keyword
      group by c.keyword order by max(c.volume) desc nulls last, c.keyword limit 1000`),
    keywordRun: (await q(`select max(collected_at) as at, count(distinct competitor) filter (where source = 'competitor')::int as compared,
        count(distinct keyword) filter (where source = 'own')::int as own_keywords
      from raw_keyword_ideas where project_id = $1
        and run_id = (select run_id from raw_keyword_ideas where project_id = $1 and source = 'competitor' order by collected_at desc limit 1)`))[0] ?? {},
    links: await q(`with lr as (select run_id from raw_referring_domains where project_id = $1 order by collected_at desc limit 1),
      rd as (select * from raw_referring_domains where project_id = $1 and run_id = (select run_id from lr)),
      mine as (select domain from rd where role = 'own' and lost_date is null
               union select regexp_replace(lower(split_part(split_part(source_url, '//', 2), '/', 1)), '^www\\.', '') from mentions where project_id = $1 and kind = 'link')
      select domain, count(distinct target)::int as linking, array_agg(distinct target order by target) as targets, max(rank) as rank,
        max(spam_score) as spam, sum(backlinks)::int as backlinks, min(first_seen) as first_seen
      from rd where role = 'competitor' and lost_date is null and domain not in (select domain from mine)
        and not exists (select 1 from unnest($2::text[]) x where rd.domain = x or rd.domain like '%.' || x)
      group by domain order by count(distinct target) desc, max(rank) desc nulls last limit 1000`, [excluded]),
    linkRun: (await q(`with lr as (select run_id from raw_referring_domains where project_id = $1 order by collected_at desc limit 1)
      select max(collected_at) as at, count(distinct target) filter (where role = 'competitor')::int as compared,
        count(distinct domain) filter (where role = 'own')::int as own_domains,
        (count(*) filter (where role = 'competitor'))::float / nullif(count(distinct target) filter (where role = 'competitor'), 0) as per_competitor
      from raw_referring_domains where project_id = $1 and run_id = (select run_id from lr)`))[0] ?? {},
  }));

  // ---- keyword gap ----
  const kwRows = d.keywords.map((r) => {
    const positions = (r.positions as Record<string, { r: number | null; u: string | null }>) ?? {};
    const yours = num(r.own_rank) ?? (num(r.gsc_pos) !== null ? Math.round(num(r.gsc_pos)!) : null);
    const best = num(r.best);
    const gap: Gap = yours === null ? (Number(r.ranking) >= 2 ? "missing" : "untapped") : best !== null && yours > best ? "weak" : "strong";
    return { r, positions, yours, gap, fromGsc: num(r.own_rank) === null && yours !== null };
  });
  // Competitor columns: the ones ranking for the most of these keywords, at most five.
  const byCount = new Map<string, number>();
  for (const k of kwRows) for (const c of Object.keys(k.positions)) byCount.set(c, (byCount.get(c) ?? 0) + 1);
  const shownComps = Array.from(byCount.entries()).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([c]) => c);
  const counts = { missing: 0, untapped: 0, weak: 0, strong: 0 } as Record<Gap, number>;
  for (const k of kwRows) counts[k.gap] += 1;
  const missingVolume = kwRows.filter((k) => k.gap === "missing" || k.gap === "untapped").reduce((a, k) => a + Number(k.r.volume ?? 0), 0);

  const kwTable: DRow[] = kwRows.map(({ r, positions, yours, gap, fromGsc }) => {
    const others = Object.keys(positions).filter((c) => !shownComps.includes(c));
    return {
      key: str(r.keyword),
      facet: gap,
      values: [str(r.keyword), GAP_LABEL[gap], str(r.intent), yours, ...shownComps.map((c) => positions[c]?.r ?? null), Number(r.ranking), num(r.volume), num(r.kd), num(r.cpc)],
      cells: [
        <span key="k">{str(r.keyword)}</span>,
        <span key="g" className="gap-tag" title={GAP_HELP[gap]}>{GAP_LABEL[gap]}</span>,
        <Intent key="i" value={r.intent} />,
        <span key="y" title={fromGsc ? "average Search Console position, last 90 days" : r.own_url ? pathOf(r.own_url) : undefined}>
          <Position value={yours} />{fromGsc ? <span className="muted small"> GSC</span> : null}
        </span>,
        ...shownComps.map((c) => <Position key={c} value={positions[c]?.r ?? null} title={positions[c]?.u ? `${c}${pathOf(positions[c]!.u)}` : `${c} does not rank in the collected data`} />),
        <span key="n" title={others.length ? `also: ${others.join(", ")}` : undefined}>{fmtInt(r.ranking)}</span>,
        <span key="v">{fmtInt(r.volume)}</span>,
        <Difficulty key="d" value={r.kd} />,
        <span key="c">{r.cpc == null ? "–" : fmtUsd(r.cpc)}</span>,
      ],
    };
  });

  // ---- backlink gap ----
  const compared = Number(d.linkRun.compared ?? 0);
  const linkTable: DRow[] = d.links.map((r) => {
    const targets = (r.targets as string[]) ?? [];
    return {
      key: str(r.domain),
      facet: Number(r.linking) >= 2 ? "multi" : "single",
      values: [str(r.domain), Number(r.linking), targets.join(" "), num(r.rank), num(r.backlinks), num(r.spam), str(r.first_seen).slice(0, 10)],
      cells: [
        <a key="d" href={`https://${str(r.domain)}`} target="_blank" rel="noreferrer">{str(r.domain)}</a>,
        <span key="l">{fmtInt(r.linking)}<span className="muted small"> of {fmtInt(compared)}</span></span>,
        <span key="t" className="small secondary">{targets.slice(0, 4).join(", ")}{targets.length > 4 ? ` +${targets.length - 4}` : ""}</span>,
        <span key="r">{fmtInt(r.rank)}</span>,
        <span key="b">{fmtInt(r.backlinks)}</span>,
        <span key="s" title="DataForSEO spam score, 0-100">{r.spam == null ? "–" : fmtInt(r.spam)}</span>,
        <span key="f" className="nowrap muted">{fmtDay(r.first_seen)}</span>,
      ],
    };
  });
  const multi = linkTable.filter((r) => r.facet === "multi").length;

  return (
    <>
      <p className="page-sub">Where competitors win and you don't: the keywords they rank for and the sites that link to them.
        Competitors come from the project settings: {competitors.length ? competitors.join(", ") : "none configured"}.</p>

      <div className="kpis cols-3">
        <StatTile label="Keywords competitors rank for" value={fmtInt(kwRows.length)}
          foot={d.keywordRun.at ? `in your topic, ${fmtInt(d.keywordRun.compared)} competitors, ${fmtDay(d.keywordRun.at)}` : "collected by the monthly run"} />
        <StatTile label="Missing or untapped" value={fmtInt(counts.missing + counts.untapped)}
          foot={`${fmtInt(missingVolume)} searches a month you don't rank for`} />
        <StatTile label="Weak" value={fmtInt(counts.weak)} foot="you rank below the best competitor" />
        <StatTile label="Strong" value={fmtInt(counts.strong)} foot="you rank at or above every competitor" />
        <StatTile label="Link gap domains" value={fmtInt(linkTable.length)}
          foot={`${fmtInt(multi)} link to two or more competitors`} />
        <StatTile label="Your referring domains" value={fmtInt(d.linkRun.own_domains)}
          foot={d.linkRun.per_competitor ? `top ${fmtInt(d.linkRun.per_competitor)} collected per competitor` : "collected by the monthly run"} />
      </div>

      <Section title="Keyword gap" note={<>Keywords in your topic that your competitors rank for, with your position next to theirs.
        Your position comes from DataForSEO, or from Search Console (marked GSC) when DataForSEO has none. Missing and untapped keywords
        are pages to write; weak ones are pages to improve. Hover a position to see the ranking page.</>}>
        <DataTable filename="keyword-gap" searchLabel="Filter keywords" defaultSort={{ col: 5 + shownComps.length, desc: true }}
          facets={(["missing", "untapped", "weak", "strong"] as Gap[]).map((g) => ({ key: g, label: GAP_LABEL[g] }))}
          columns={[{ label: "Keyword" }, { label: "Gap" }, { label: "Intent", title: "I informational · N navigational · C commercial · T transactional" },
            { label: "You", num: true }, ...shownComps.map((c) => ({ label: c.replace(/\.(com|in|co\.in|tech|io|co)$/, ""), num: true, title: c })),
            { label: "Ranking", num: true, title: "competitors ranking for it" }, { label: "Volume", num: true }, { label: "KD", num: true, title: "keyword difficulty, 0-100" },
            { label: "CPC", num: true }]}
          rows={kwTable}
          empty={d.keywordRun.at ? "No keyword matches." : "No competitor keywords collected yet. They arrive with the next monthly run for a project with competitors configured."} />
      </Section>

      <Section title="Backlink gap" note={<>Sites that link to your competitors but not to you, strongest and most widely linking first.
        Your own links and sites that link to everything (YouTube, Wikipedia, LinkedIn…) are left out. Each one is a place to get listed, quoted or reviewed.</>}>
        <DataTable filename="backlink-gap" searchLabel="Filter domains" defaultFacet={multi > 0 ? "multi" : "all"}
          facets={[{ key: "multi", label: "Link to 2+ competitors" }, { key: "single", label: "Link to 1" }]}
          columns={[{ label: "Referring domain" }, { label: "Competitors", num: true }, { label: "Links to", sort: false }, { label: "Authority", num: true, title: "DataForSEO domain rank, 0-100" },
            { label: "Backlinks", num: true }, { label: "Spam", num: true }, { label: "Since", num: true }]}
          rows={linkTable}
          empty={d.linkRun.at ? "No domain matches." : "No referring domains collected yet. They arrive with the next monthly run for a project with competitors configured."} />
        {d.linkRun.at ? <p className="muted small">Collected {fmtDay(d.linkRun.at)} from {fmtInt(compared)} competitors.</p> : null}
      </Section>
    </>
  );
}

