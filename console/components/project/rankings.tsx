import { Empty, Section, StatTile } from "@/components/ui";
import { withProject } from "@/lib/db";
import { fmtDay, fmtInt, str } from "@/lib/format";
import { citesUs, ourRank, projectDomains } from "@/lib/sql";
import type { TabProps } from "./overview";

type Top = { d: string; u: string; rk: number };

export async function RankingsTab({ projectId, range, project }: TabProps) {
  const domains = projectDomains(project);
  const rows = await withProject(projectId, (q) => q(`
    with s as (
      select query, collected_at, results, ai_overview_present, ai_overview_citations,
        row_number() over (partition by lower(query) order by collected_at desc) as rn
      from raw_serp where project_id = $1
    )
    select cur.query, cur.collected_at, ${ourRank("cur.results", "$3")} as rank,
      ${ourRank("prev.results", "$3")} as prev_rank, prev.collected_at as prev_at,
      cur.ai_overview_present, ${citesUs("cur.ai_overview_citations", "$3")} as ai_cites_us,
      jsonb_array_length(coalesce(cur.ai_overview_citations, '[]'::jsonb)) as ai_sources,
      (select jsonb_agg(x order by x.rk) from (
         select r->>'domain' as d, r->>'url' as u, (r->>'rank')::int as rk
         from jsonb_array_elements(coalesce(cur.results, '[]'::jsonb)) r order by (r->>'rank')::int limit 3) x) as top3,
      (select array_agg(coalesce(${ourRank("h.results", "$3")}::text, '–') order by h.collected_at)
         from s h where lower(h.query) = lower(cur.query) and h.collected_at >= now() - make_interval(days => $2::int)) as history
    from s cur left join s prev on lower(prev.query) = lower(cur.query) and prev.rn = 2
    where cur.rn = 1
    order by rank nulls last, cur.query`, [range, domains]));

  const ranked = rows.filter((r) => r.rank != null);
  const top3 = ranked.filter((r) => Number(r.rank) <= 3).length;
  const top10 = ranked.filter((r) => Number(r.rank) <= 10).length;
  const aio = rows.filter((r) => r.ai_overview_present).length;
  const aioUs = rows.filter((r) => r.ai_cites_us).length;

  return (
    <>
      <div className="kpis">
        <StatTile label="Queries tracked" value={fmtInt(rows.length)} foot="Google India, desktop, top 20, weekly" />
        <StatTile label="In the top 3" value={fmtInt(top3)} foot={`${fmtInt(top10)} in the top 10`} />
        <StatTile label="Not in the top 20" value={fmtInt(rows.length - ranked.length)} foot="the site is absent from the results checked" />
        <StatTile label="AI Overview shown" value={fmtInt(aio)} foot={`cites the site on ${fmtInt(aioUs)}`} />
      </div>
      <Section title="Rankings" note={`Latest check per query, against the check before it. History lists every check in the last ${range} days, oldest first.`}>
        <div className="scroll"><table>
          <thead><tr>
            <th>Query</th><th className="num">Rank</th><th className="num">Previous</th><th>History</th>
            <th>AI Overview</th><th>Top results</th><th>Checked</th>
          </tr></thead>
          <tbody>
            {rows.map((r) => {
              const rank = r.rank == null ? null : Number(r.rank);
              const prev = r.prev_rank == null ? null : Number(r.prev_rank);
              const move = rank != null && prev != null ? prev - rank : null;
              return (
                <tr key={str(r.query)}>
                  <td>{str(r.query)}</td>
                  <td className="num">{rank ?? <span className="muted">&gt;20</span>}</td>
                  <td className="num muted">
                    {prev ?? (r.prev_at ? ">20" : "–")}
                    {move ? <span className={`delta ${move > 0 ? "up-good" : "down-bad"}`}> {move > 0 ? "▲" : "▼"}{Math.abs(move)}</span> : null}
                  </td>
                  <td className="muted small nowrap">{Array.isArray(r.history) ? (r.history as string[]).join(" → ") : ""}</td>
                  <td>{r.ai_overview_present
                    ? <span className={`pill ${r.ai_cites_us ? "ok" : "warning"}`}>{r.ai_cites_us ? "cites the site" : `shown, ${fmtInt(r.ai_sources)} sources`}</span>
                    : <span className="muted">none</span>}</td>
                  <td className="small">
                    {((r.top3 as Top[] | null) ?? []).map((t) => (
                      <div key={t.rk} className="wrap-anywhere"><span className="muted">{t.rk}.</span> <a href={t.u} target="_blank" rel="noreferrer">{t.d}</a></div>
                    ))}
                  </td>
                  <td className="nowrap muted">{fmtDay(r.collected_at)}</td>
                </tr>
              );
            })}
            {rows.length === 0 && <Empty cols={7}>No SERP checks yet. The weekly monitor checks the tracked queries.</Empty>}
          </tbody>
        </table></div>
      </Section>
    </>
  );
}
