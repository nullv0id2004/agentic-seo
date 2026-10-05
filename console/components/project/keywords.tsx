import { Empty, Section, StatTile } from "@/components/ui";
import { withProject } from "@/lib/db";
import { fmtDay, fmtInt, fmtPos, fmtUsd, pathOf, str } from "@/lib/format";
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
  }));
  const rows = d.rows;
  const mapped = rows.filter((r) => r.mapped_url).length;
  const priced = rows.filter((r) => r.volume != null || r.volume_low != null).length;
  const metricsAt = rows.find((r) => r.metrics_at)?.metrics_at;
  const ranking = rows.filter((r) => r.serp_rank != null && Number(r.serp_rank) <= 20).length;

  return (
    <>
      <div className="kpis">
        <StatTile label="Tracked keywords" value={fmtInt(rows.length)} foot="from seeds, Search Console and the keyword analyst" />
        <StatTile label="Mapped to a page" value={fmtInt(mapped)} foot={rows.length ? `${Math.round((mapped / rows.length) * 100)}% of tracked` : "–"} />
        <StatTile label="With search volume" value={fmtInt(priced)} foot="quarterly DataForSEO refresh" />
        <StatTile label="Ranking in the top 20" value={fmtInt(ranking)} foot="from the latest SERP check" />
      </div>
      <Section title="Keywords" note={`Volume, difficulty and CPC come from the latest DataForSEO pull (India). Clicks, impressions and position are from Search Console, last ${range} days. AI volume is how often the term is asked of AI assistants.`}>
        <div className="scroll"><table>
          <thead><tr>
            <th>Keyword</th><th>Intent</th><th>Cluster</th><th>Mapped page</th>
            <th className="num">Volume</th><th className="num">Difficulty</th><th className="num">CPC</th><th className="num">AI volume</th>
            <th className="num">SERP rank</th><th className="num">GSC clicks</th><th className="num">GSC impr.</th><th className="num">GSC position</th>
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={str(r.keyword)}>
                <td>{str(r.keyword)} {r.blocked_for_index ? <span className="pill warning">blocked</span> : null}</td>
                <td className="muted">{str(r.intent)}</td>
                <td className="muted">{str(r.cluster)}</td>
                <td className="wrap-anywhere">{r.mapped_url ? pathOf(r.mapped_url) : <span className="muted">unmapped</span>}</td>
                <td className="num">{r.volume_is_range ? `${fmtInt(r.volume_low)}–${fmtInt(r.volume_high)}` : fmtInt(r.volume)}</td>
                <td className="num">{fmtInt(r.difficulty)}</td>
                <td className="num">{r.cpc == null ? "–" : fmtUsd(r.cpc)}</td>
                <td className="num">{fmtInt(r.ai_search_volume)}</td>
                <td className="num">{r.serp_rank != null ? fmtInt(r.serp_rank) : r.tracked_in_serp ? <span className="muted">not in top 20</span> : <span className="muted">not tracked</span>}</td>
                <td className="num">{fmtInt(r.clicks)}</td><td className="num">{fmtInt(r.impressions)}</td><td className="num">{fmtPos(r.position)}</td>
              </tr>
            ))}
            {rows.length === 0 && <Empty cols={12}>No keywords yet. The quarterly keyword run builds the list.</Empty>}
          </tbody>
        </table></div>
        {metricsAt ? <p className="muted small">Keyword metrics last refreshed {fmtDay(metricsAt)}.</p> : null}
      </Section>
    </>
  );
}
