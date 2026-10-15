import { Difficulty, Intent, Position } from "@/components/badges";
import { DataTable, type DRow } from "@/components/datatable";
import { Section, StatTile } from "@/components/ui";
import { withProject } from "@/lib/db";
import { fmtDay, fmtInt, fmtPos, fmtUsd, num, pathOf, str } from "@/lib/format";
import { GSC, ourRank, projectDomains } from "@/lib/sql";
import type { TabProps } from "./overview";

export async function KeywordsTab({ projectId, range, project }: TabProps) {
  const domains = projectDomains(project);
  const d = await withProject(projectId, async (q) => ({
    rows: await q(`with ${GSC},
      km as (select distinct on (lower(keyword)) lower(keyword) as k, volume, volume_is_range, volume_low, volume_high, difficulty, cpc, collected_at
             from raw_keyword_metrics where project_id = $1 order by lower(keyword), collected_at desc),
      ak as (select distinct on (lower(keyword)) lower(keyword) as k, ai_search_volume
             from raw_ai_keyword_metrics where project_id = $1 order by lower(keyword), collected_at desc),
      sp as (select distinct on (lower(query)) lower(query) as k, results, ai_overview_present
             from raw_serp where project_id = $1 order by lower(query), collected_at desc),
      gq as (select lower(query) as k, sum(clicks)::int as clicks, sum(impressions)::int as impressions,
                    sum(position * impressions) / nullif(sum(impressions), 0) as position
             from gsc_queries, gsc_w where date between s and e group by lower(query))
      select kw.keyword, kw.intent, kw.cluster, kw.mapped_url, kw.blocked_for_index, kw.updated_at,
        km.volume, km.volume_is_range, km.volume_low, km.volume_high, km.difficulty, km.cpc, km.collected_at as metrics_at,
        ak.ai_search_volume, sp.ai_overview_present, ${ourRank("sp.results", "$3")} as serp_rank, sp.k is not null as tracked_in_serp,
        gq.clicks, gq.impressions, gq.position
      from keywords kw
      left join km on km.k = lower(kw.keyword) left join ak on ak.k = lower(kw.keyword)
      left join sp on sp.k = lower(kw.keyword) left join gq on gq.k = lower(kw.keyword)
      where kw.project_id = $1
      order by km.volume desc nulls last, kw.keyword`, [range, domains]),
    ideas: await q(`with lr as (select run_id from raw_keyword_ideas where project_id = $1 and source <> 'own' order by collected_at desc limit 1)
      select i.keyword, max(i.search_volume) as volume, max(i.keyword_difficulty) as difficulty, max(i.cpc) as cpc,
        (array_agg(i.intent) filter (where i.intent is not null))[1] as intent,
        bool_or(i.source = 'idea') as from_seeds,
        coalesce(jsonb_agg(jsonb_build_object('d', i.competitor, 'r', i.competitor_rank, 'u', i.competitor_url) order by i.competitor_rank)
                 filter (where i.source = 'competitor'), '[]'::jsonb) as competitors,
        exists (select 1 from keywords k where k.project_id = $1 and lower(k.keyword) = i.keyword) as tracked,
        max(i.collected_at) as at
      from raw_keyword_ideas i where i.project_id = $1 and i.run_id = (select run_id from lr) and i.source <> 'own'
      group by i.keyword order by max(i.search_volume) desc nulls last, i.keyword limit 500`),
  }));
  const rows = d.rows;
  const mapped = rows.filter((r) => r.mapped_url).length;
  const priced = rows.filter((r) => r.volume != null || r.volume_low != null).length;
  const metricsAt = rows.find((r) => r.metrics_at)?.metrics_at;
  const ranking = rows.filter((r) => r.serp_rank != null && Number(r.serp_rank) <= 20).length;

  return (
    <>
      <div className="kpis cols-4">
        <StatTile label="Tracked keywords" value={fmtInt(rows.length)} foot="from seeds, Search Console and the keyword analyst" />
        <StatTile label="Mapped to a page" value={fmtInt(mapped)} foot={rows.length ? `${Math.round((mapped / rows.length) * 100)}% of tracked` : "–"} />
        <StatTile label="With search volume" value={fmtInt(priced)} foot="quarterly DataForSEO refresh" />
        <StatTile label="Ranking in the top 20" value={fmtInt(ranking)} foot="from the latest SERP check" />
      </div>
      <Section title="Keywords" note={`Volume, difficulty and CPC come from the latest DataForSEO pull (India). Clicks, impressions and position are from Search Console, last ${range} days. AI volume is how often the term is asked of AI assistants.`}>
        <DataTable filename="keywords" searchLabel="Filter keywords" defaultSort={{ col: 4, desc: true }}
          facets={[{ key: "mapped", label: "Mapped to a page" }, { key: "unmapped", label: "Unmapped (content to write)" }]}
          columns={[{ label: "Keyword" }, { label: "Intent", title: "I informational · N navigational · C commercial · T transactional" }, { label: "Cluster" }, { label: "Mapped page" },
            { label: "Volume", num: true }, { label: "KD", num: true, title: "keyword difficulty, 0-100" }, { label: "CPC", num: true }, { label: "AI volume", num: true },
            { label: "Google rank", num: true, title: "from the latest SERP check" }, { label: "GSC clicks", num: true }, { label: "GSC impr.", num: true }, { label: "GSC position", num: true }]}
          rows={rows.map((r): DRow => {
            const vol = r.volume_is_range ? num(r.volume_high) : num(r.volume);
            return {
              key: str(r.keyword),
              facet: r.mapped_url ? "mapped" : "unmapped",
              values: [str(r.keyword), str(r.intent), str(r.cluster), r.mapped_url ? pathOf(r.mapped_url) : "", vol, num(r.difficulty), num(r.cpc), num(r.ai_search_volume),
                num(r.serp_rank), num(r.clicks), num(r.impressions), num(r.position)],
              cells: [
                <span key="k">{str(r.keyword)} {r.blocked_for_index ? <span className="pill warning">blocked</span> : null}</span>,
                <Intent key="i" value={r.intent} />,
                <span key="c" className="muted">{str(r.cluster)}</span>,
                <span key="m" className="wrap-anywhere">{r.mapped_url ? pathOf(r.mapped_url) : <span className="muted">unmapped</span>}</span>,
                <span key="v">{r.volume_is_range ? `${fmtInt(r.volume_low)}–${fmtInt(r.volume_high)}` : fmtInt(r.volume)}</span>,
                <Difficulty key="d" value={r.difficulty} />,
                <span key="p">{r.cpc == null ? "–" : fmtUsd(r.cpc)}</span>,
                <span key="a">{fmtInt(r.ai_search_volume)}</span>,
                r.serp_rank != null ? <Position key="s" value={r.serp_rank} /> : <span key="s" className="muted small">{r.tracked_in_serp ? "not in top 20" : "not tracked"}</span>,
                <span key="gc">{fmtInt(r.clicks)}</span>, <span key="gi">{fmtInt(r.impressions)}</span>, <span key="gp">{fmtPos(r.position)}</span>,
              ],
            };
          })}
          empty="No keywords yet. The quarterly keyword run builds the list." />
        {metricsAt ? <p className="muted small">Keyword metrics last refreshed {fmtDay(metricsAt)}.</p> : null}
      </Section>
      <Section title="Keyword opportunities" note={`From the last discovery${d.ideas[0]?.at ? ` (${fmtDay(d.ideas[0].at)})` : ""}: ideas grown from your seeds, and keywords the sites that outrank you get traffic from. The highest-volume ones join the tracked list; those no page fits stay unmapped as content to write. The full side-by-side comparison is under Competitive research.`}>
        <DataTable filename="keyword-opportunities" searchLabel="Filter keywords" defaultSort={{ col: 1, desc: true }}
          facets={[{ key: "seed", label: "From your seeds" }, { key: "competitor", label: "From competitors" }]}
          columns={[{ label: "Keyword" }, { label: "Volume", num: true }, { label: "KD", num: true }, { label: "CPC", num: true }, { label: "Intent" },
            { label: "Competitors ranking", sort: false }, { label: "Tracked" }]}
          rows={d.ideas.map((r): DRow => {
            const comps = (r.competitors as { d: string; r: number | null; u: string | null }[]) ?? [];
            return {
              key: str(r.keyword),
              facet: r.from_seeds ? "seed" : "competitor",
              values: [str(r.keyword), num(r.volume), num(r.difficulty), num(r.cpc), str(r.intent), comps.map((c) => `${c.d} #${c.r ?? ""}`).join("; "), r.tracked ? "yes" : "no"],
              cells: [
                <span key="k">{str(r.keyword)}{r.from_seeds ? <div className="muted small">from your seeds</div> : null}</span>,
                <span key="v">{fmtInt(r.volume)}</span>, <Difficulty key="d" value={r.difficulty} />,
                <span key="c">{r.cpc == null ? "–" : fmtUsd(r.cpc)}</span>, <Intent key="i" value={r.intent} />,
                <span key="cr" className="small">{comps.slice(0, 3).map((c, i) => <div key={i} className="wrap-anywhere">{c.d} <span className="muted">#{str(c.r)}</span></div>)}{comps.length === 0 ? <span className="muted">none</span> : null}</span>,
                r.tracked ? <span key="t" className="pill ok">yes</span> : <span key="t" className="muted">no</span>,
              ],
            };
          })}
          empty={<>No discovery yet. The monthly or quarterly keyword run finds opportunities; run it from SSH with: python worker.py run {str(project.slug)} quarterly_keyword</>} />
      </Section>
    </>
  );
}
