import { LineChart } from "@/components/charts";
import { Empty, Section } from "@/components/ui";
import { withProject } from "@/lib/db";
import { fmtInt, fmtPct, fmtPos, num, pathOf, str } from "@/lib/format";
import { GSC, GSC_DAILY } from "@/lib/sql";
import type { TabProps } from "./overview";

export async function SearchTab({ projectId, range }: TabProps) {
  const d = await withProject(projectId, async (q) => ({
    daily: await q(GSC_DAILY, [range]),
    pages: await q(`with ${GSC},
      cur as (select page, sum(clicks)::int as clicks, sum(impressions)::int as impressions,
                     sum(position * impressions) / nullif(sum(impressions), 0) as position
              from gsc_pages, gsc_w where date between s and e group by page),
      prev as (select page, sum(clicks)::int as clicks, sum(position * impressions) / nullif(sum(impressions), 0) as position
               from gsc_pages, gsc_w where date between ps and pe group by page)
      select cur.*, prev.clicks as prev_clicks, prev.position as prev_position
      from cur left join prev using (page) order by cur.clicks desc, cur.impressions desc limit 100`, [range]),
    queries: await q(`with ${GSC},
      cur as (select query, sum(clicks)::int as clicks, sum(impressions)::int as impressions,
                     sum(position * impressions) / nullif(sum(impressions), 0) as position, (array_agg(page order by clicks desc, impressions desc))[1] as page
              from gsc_queries, gsc_w where date between s and e group by query),
      prev as (select query, sum(position * impressions) / nullif(sum(impressions), 0) as position
               from gsc_queries, gsc_w where date between ps and pe group by query)
      select cur.*, prev.position as prev_position
      from cur left join prev using (query) order by cur.clicks desc, cur.impressions desc limit 100`, [range]),
    anon: (await q(`with ${GSC}
      select (select coalesce(sum(clicks), 0) from gsc_pages, gsc_w where date between s and e)::int as page_clicks,
             (select coalesce(sum(clicks), 0) from gsc_queries, gsc_w where date between s and e)::int as query_clicks,
             (select coalesce(sum(impressions), 0) from gsc_pages, gsc_w where date between s and e)::int as page_impr,
             (select coalesce(sum(impressions), 0) from gsc_queries, gsc_w where date between s and e)::int as query_impr`, [range]))[0] ?? {},
  }));
  const x = d.daily.map((r) => str(r.date));
  const s = (k: string) => d.daily.map((r) => (r.collected ? num(r[k]) : null));
  const ctr = d.daily.map((r) => (r.collected && Number(r.impressions) > 0 ? Number(r.clicks) / Number(r.impressions) : null));
  const hiddenImpr = Number(d.anon.page_impr ?? 0) - Number(d.anon.query_impr ?? 0);

  return (
    <>
      <div className="grid-2">
        <LineChart title="Clicks per day" x={x} series={[{ label: "Clicks", slot: 1, values: s("clicks") }]} />
        <LineChart title="Impressions per day" x={x} series={[{ label: "Impressions", slot: 1, values: s("impressions") }]} />
        <LineChart title="Click-through rate per day" x={x} format="pct" series={[{ label: "CTR", slot: 1, values: ctr }]} />
        <LineChart title="Average position per day" note="lower is better" x={x} invert format="pos" series={[{ label: "Position", slot: 1, values: s("position") }]} />
      </div>

      <Section title="Pages" note={`Last ${range} days against the ${range} days before. Position is weighted by impressions.`}>
        <div className="scroll"><table>
          <thead><tr><th>Page</th><th className="num">Clicks</th><th className="num">Prev clicks</th><th className="num">Impressions</th><th className="num">CTR</th><th className="num">Position</th><th className="num">Prev position</th></tr></thead>
          <tbody>
            {d.pages.map((p) => (
              <tr key={str(p.page)}>
                <td className="wrap-anywhere"><a href={str(p.page)} target="_blank" rel="noreferrer">{pathOf(p.page)}</a></td>
                <td className="num">{fmtInt(p.clicks)}</td><td className="num muted">{fmtInt(p.prev_clicks)}</td>
                <td className="num">{fmtInt(p.impressions)}</td>
                <td className="num">{Number(p.impressions) > 0 ? fmtPct(Number(p.clicks) / Number(p.impressions)) : "–"}</td>
                <td className="num">{fmtPos(p.position)}</td><td className="num muted">{fmtPos(p.prev_position)}</td>
              </tr>
            ))}
            {d.pages.length === 0 && <Empty cols={7}>No Search Console page data in this range.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Search queries"
        note={hiddenImpr > 0
          ? `Google withholds rare queries for privacy: ${fmtInt(hiddenImpr)} of ${fmtInt(d.anon.page_impr)} impressions in this range have no query attached.`
          : "Queries people searched before seeing the site."}>
        <div className="scroll"><table>
          <thead><tr><th>Query</th><th className="num">Clicks</th><th className="num">Impressions</th><th className="num">CTR</th><th className="num">Position</th><th className="num">Prev position</th><th>Top page</th></tr></thead>
          <tbody>
            {d.queries.map((r) => (
              <tr key={str(r.query)}>
                <td>{str(r.query)}</td>
                <td className="num">{fmtInt(r.clicks)}</td><td className="num">{fmtInt(r.impressions)}</td>
                <td className="num">{Number(r.impressions) > 0 ? fmtPct(Number(r.clicks) / Number(r.impressions)) : "–"}</td>
                <td className="num">{fmtPos(r.position)}</td><td className="num muted">{fmtPos(r.prev_position)}</td>
                <td className="muted wrap-anywhere">{pathOf(r.page)}</td>
              </tr>
            ))}
            {d.queries.length === 0 && <Empty cols={7}>No query rows in this range. Google hides queries with very few impressions.</Empty>}
          </tbody>
        </table></div>
      </Section>
    </>
  );
}
