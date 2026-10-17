import { LineChart } from "@/components/charts";
import { DataTable, type DRow } from "@/components/datatable";
import { Section, Sparkline, StatTile } from "@/components/ui";
import { registrable } from "@/lib/domains";
import { withProject } from "@/lib/db";
import { fmtDay, fmtInt, num, str } from "@/lib/format";
import { citesUs, ourRank, projectDomains } from "@/lib/sql";
import type { TabProps } from "./overview";

type Result = { rank: number | null; domain: string | null; url: string | null };

// Share of clicks a result at each position gets (a common organic CTR curve). Visibility is the share of the clicks
// the tracked keywords could bring that the site's positions would get: 100% means first for every keyword.
const CTR = [0, 0.28, 0.15, 0.11, 0.08, 0.07, 0.05, 0.04, 0.03, 0.03, 0.025];
const ctr = (pos: number | null) => (pos == null ? 0 : pos <= 10 ? CTR[pos] : pos <= 20 ? 0.01 : 0);
const visibility = (positions: (number | null)[]) =>
  positions.length ? (100 * positions.reduce<number>((a, p) => a + ctr(p), 0)) / (positions.length * CTR[1]) : null;

export async function RankingsTab({ projectId, range, project }: TabProps) {
  const domains = projectDomains(project);
  const competitors = new Set(((project.competitors as string[] | null) ?? []).map(registrable));
  const d = await withProject(projectId, async (q) => ({
    // the latest check of each keyword on each day in the range
    daily: await q(`
      with s as (
        select lower(query) as q, collected_at::date as day, results,
          row_number() over (partition by lower(query), collected_at::date order by collected_at desc) as rn
        from raw_serp where project_id = $1 and collected_at >= current_date - make_interval(days => $2::int))
      select q, day::text as day, ${ourRank("results", "$3")} as rank from s where rn = 1 order by day`, [range, domains]),
    latest: await q(`
      with s as (
        select query, collected_at, results, ai_overview_present, ai_overview_citations,
          row_number() over (partition by lower(query) order by collected_at desc) as rn
        from raw_serp where project_id = $1)
      select cur.query, cur.collected_at, cur.results, ${ourRank("cur.results", "$2")} as rank, ${ourRank("prev.results", "$2")} as prev_rank,
        prev.collected_at as prev_at, cur.ai_overview_present, ${citesUs("cur.ai_overview_citations", "$2")} as ai_cites_us,
        (select r->>'url' from jsonb_array_elements(coalesce(cur.results, '[]'::jsonb)) r
          where regexp_replace(lower(r->>'domain'), '^www\\.', '') = any($2::text[]) order by (r->>'rank')::int limit 1) as our_url
      from s cur left join s prev on lower(prev.query) = lower(cur.query) and prev.rn = 2
      where cur.rn = 1 order by rank nulls last, cur.query`, [domains]),
  }));

  // visibility per day, and each keyword's positions over the range
  const days = Array.from(new Set(d.daily.map((r) => str(r.day)))).sort();
  const byDay = new Map<string, (number | null)[]>();
  const byKw = new Map<string, Map<string, number | null>>();
  for (const r of d.daily) {
    const day = str(r.day);
    const rank = num(r.rank);
    byDay.set(day, [...(byDay.get(day) ?? []), rank]);
    if (!byKw.has(str(r.q))) byKw.set(str(r.q), new Map());
    byKw.get(str(r.q))!.set(day, rank);
  }
  const visSeries = days.map((day) => visibility(byDay.get(day) ?? []));

  const ranks = d.latest.map((r) => num(r.rank));
  const ranked = ranks.filter((r): r is number => r != null);
  const vis = visibility(ranks);
  const prevVis = visSeries.length > 1 ? visSeries[visSeries.length - 2] : null;
  const top10 = ranked.filter((r) => r <= 10).length;
  const avg = ranked.length ? ranked.reduce((a, b) => a + b, 0) / ranked.length : null;

  // who else ranks for these keywords: each domain's best position per keyword in the latest checks. The site is
  // matched by host (korum.worldhire.com is not worldhire.com); everyone else by registrable domain.
  const ownHost = domains[0];
  const isOwn = (host: string) => {
    const h = host.toLowerCase().replace(/^www\./, "");
    return domains.some((dd) => h === dd || h.endsWith(`.${dd}`));
  };
  const keyOf = (host: string) => (isOwn(host) ? ownHost : registrable(host));
  const share = new Map<string, { positions: number[]; keywords: number }>();
  for (const r of d.latest) {
    const best = new Map<string, number>();
    for (const x of (r.results as Result[] | null) ?? []) {
      if (!x.domain || x.rank == null) continue;
      const dom = keyOf(x.domain);
      if (!best.has(dom) || x.rank < best.get(dom)!) best.set(dom, x.rank);
    }
    for (const [dom, pos] of best) {
      const s = share.get(dom) ?? { positions: [], keywords: 0 };
      s.positions.push(pos);
      s.keywords += 1;
      share.set(dom, s);
    }
  }
  const ownKeys = new Set([ownHost]);
  const shareRows = Array.from(share.entries())
    .map(([dom, s]) => ({ dom, vis: (100 * s.positions.reduce((a, p) => a + ctr(p), 0)) / (Math.max(1, d.latest.length) * CTR[1]),
                          top10: s.positions.filter((p) => p <= 10).length, avg: s.positions.reduce((a, b) => a + b, 0) / s.positions.length, keywords: s.keywords }))
    .sort((a, b) => b.vis - a.vis).slice(0, 25);

  const kwRows: DRow[] = d.latest.map((r) => {
    const rank = num(r.rank);
    const prev = num(r.prev_rank);
    const move = rank != null && prev != null ? prev - rank : null;
    const hist = days.map((day) => byKw.get(str(r.query).toLowerCase())?.get(day) ?? null);
    const best = ((r.results as Result[] | null) ?? []).filter((x) => x.domain && !isOwn(str(x.domain)))
      .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99))[0];
    return {
      key: str(r.query),
      facet: rank == null ? "out" : rank <= 3 ? "top3" : rank <= 10 ? "top10" : "top20",
      cells: [
        str(r.query),
        rank ?? <span className="muted">&gt;20</span>,
        move ? <span className={`delta ${move > 0 ? "up-good" : "down-bad"}`}>{move > 0 ? "▲" : "▼"}{Math.abs(move)}</span>
          : <span className="muted">{prev == null && r.prev_at && rank != null ? "new" : "–"}</span>,
        <span key="t" className="spark">{hist.filter((v) => v != null).length > 1
          ? <Sparkline values={hist.map((v) => (v == null ? null : 21 - v))} label={`Position of ${str(r.query)} over ${days.length} days, higher is better`} />
          : <span className="muted small">{hist.some((v) => v != null) ? "one check" : "–"}</span>}</span>,
        r.our_url ? <a key="u" className="small wrap-anywhere" href={str(r.our_url)} target="_blank" rel="noreferrer">{str(r.our_url).replace(/^https?:\/\/[^/]+/, "") || "/"}</a> : <span className="muted">–</span>,
        best ? <span key="b" className="small">{best.rank}. {registrable(str(best.domain))}</span> : <span className="muted">–</span>,
        r.ai_overview_present ? <span key="a" className={`pill ${r.ai_cites_us ? "ok" : "warning"}`}>{r.ai_cites_us ? "cites the site" : "shown"}</span> : <span className="muted">none</span>,
        <span key="c" className="nowrap muted">{fmtDay(r.collected_at)}</span>,
      ],
      values: [str(r.query), rank, move, null, str(r.our_url ?? ""), best ? `${best.rank} ${best.domain}` : null, r.ai_overview_present ? "yes" : "no", fmtDay(r.collected_at)],
    };
  });

  const tracked = Number(project.rank_tracking_limit ?? 0);
  return (
    <>
      <div className="kpis cols-4">
        <StatTile label="Visibility" value={vis == null ? "–" : `${vis.toFixed(1)}%`} current={vis} prev={prevVis ?? undefined} period="the check before"
          trend={visSeries.length > 1 ? visSeries : undefined} foot={vis == null ? "no checks yet" : undefined} />
        <StatTile label="Keywords tracked" value={fmtInt(d.latest.length)} foot={tracked ? `top ${tracked} checked daily, all weekly` : "weekly check; daily tracking is off"} />
        <StatTile label="In the top 10" value={fmtInt(top10)} foot={`${fmtInt(ranked.filter((r) => r <= 3).length)} in the top 3, ${fmtInt(d.latest.length - ranked.length)} not in the top 20`} />
        <StatTile label="Average position" value={avg == null ? "–" : avg.toFixed(1)} foot="of the keywords the site ranks for" />
      </div>

      <Section title="Visibility over time" note={`The share of the clicks the tracked keywords could bring that the site's positions would get, from each day's checks (Google India, desktop, top 20). Last ${range} days.`}>
        {days.length > 1 ? (
          <LineChart title="Visibility" x={days} format="dec" series={[{ label: "Visibility (%)", slot: 1, values: visSeries.map((v) => (v == null ? null : Math.round(v * 100) / 100)) }]} />
        ) : <p className="muted">One day of checks so far; the trend starts with the second.</p>}
      </Section>

      <Section title="Positions" note="Latest check of each keyword against the check before it. The trend is the position on each day checked in the range (higher on the line is better).">
        <DataTable columns={[{ label: "Keyword" }, { label: "Position", num: true }, { label: "Change", num: true }, { label: "Trend", sort: false },
          { label: "Ranking page", sort: false }, { label: "Best competitor", sort: false }, { label: "AI Overview" }, { label: "Checked" }]}
          rows={kwRows} facets={[{ key: "top3", label: "Top 3" }, { key: "top10", label: "4–10" }, { key: "top20", label: "11–20" }, { key: "out", label: "Not in top 20" }]}
          defaultSort={{ col: 1, desc: false }} filename="rank-tracking" searchLabel="Filter keywords" empty="No SERP checks yet. The daily rank check and the weekly monitor check the tracked keywords." />
      </Section>

      <Section title="Who else ranks for these keywords" note="Each domain's visibility across the same tracked keywords, from the latest checks. Configured competitors are marked; others are the sites actually taking the clicks.">
        <div className="scroll"><table>
          <thead><tr><th>Domain</th><th className="num">Visibility</th><th className="num">Keywords in top 10</th><th className="num">Keywords in top 20</th><th className="num">Average position</th></tr></thead>
          <tbody>
            {shareRows.map((s) => (
              <tr key={s.dom}>
                <td>{ownKeys.has(s.dom) ? <strong>{s.dom}</strong> : s.dom}{ownKeys.has(s.dom) ? <span className="muted small"> (you)</span> : competitors.has(s.dom) ? <span className="pill plain" style={{ marginLeft: 6 }}>competitor</span> : null}</td>
                <td className="num">{s.vis.toFixed(1)}%</td><td className="num">{fmtInt(s.top10)}</td><td className="num">{fmtInt(s.keywords)}</td><td className="num">{s.avg.toFixed(1)}</td>
              </tr>
            ))}
            {shareRows.length === 0 ? <tr><td colSpan={5} className="muted">No SERP checks yet.</td></tr> : null}
          </tbody>
        </table></div>
      </Section>
    </>
  );
}
