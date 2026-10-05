import { LineChart } from "@/components/charts";
import { Empty, Section, StatTile } from "@/components/ui";
import { withProject } from "@/lib/db";
import { fmtCompact, fmtInt, fmtPct, num, str } from "@/lib/format";
import { GA4, GA4_DAILY, GA4_TOTALS } from "@/lib/sql";
import type { TabProps } from "./overview";

export async function TrafficTab({ projectId, range }: TabProps) {
  const d = await withProject(projectId, async (q) => ({
    daily: await q(GA4_DAILY, [range]),
    totals: (await q(GA4_TOTALS, [range]))[0] ?? {},
    channels: await q(`with ${GA4},
      cur as (select channel, sum(sessions)::int as sessions, sum(engaged)::int as engaged, sum(conversions) as conversions
              from ga, ga_w where date between s and e group by channel),
      prev as (select channel, sum(sessions)::int as sessions from ga, ga_w where date between ps and pe group by channel)
      select cur.*, prev.sessions as prev_sessions from cur left join prev using (channel) order by cur.sessions desc`, [range]),
    pages: await q(`with ${GA4}
      select page_path, sum(sessions)::int as sessions, sum(engaged)::int as engaged, sum(conversions) as conversions,
        (array_agg(channel order by sessions desc))[1] as top_channel
      from ga, ga_w where date between s and e group by page_path order by sessions desc limit 100`, [range]),
  }));
  const t = d.totals;
  const prevOk = Number(t.prev_days) > 0;
  const period = `previous ${range} days`;
  const x = d.daily.map((r) => str(r.date));
  const s = (k: string) => d.daily.map((r) => (r.collected ? num(r[k]) : null));
  const rate = d.daily.map((r) => (r.collected && Number(r.sessions) > 0 ? Number(r.engaged) / Number(r.sessions) : null));
  const totalSessions = Number(t.sessions ?? 0);

  return (
    <>
      <div className="kpis">
        <StatTile label="Sessions" value={fmtCompact(t.sessions)} current={t.sessions} prev={prevOk ? t.prev_sessions : undefined} period={period} trend={s("sessions")} />
        <StatTile label="Engaged sessions" value={fmtCompact(t.engaged)} current={t.engaged} prev={prevOk ? t.prev_engaged : undefined} period={period} trend={s("engaged")} />
        <StatTile label="Engagement rate" value={totalSessions ? fmtPct(Number(t.engaged) / totalSessions) : "–"}
          current={totalSessions ? Number(t.engaged) / totalSessions : null}
          prev={prevOk && Number(t.prev_sessions) > 0 ? Number(t.prev_engaged) / Number(t.prev_sessions) : undefined} period={period} />
        <StatTile label="Conversions" value={fmtCompact(t.conversions)} current={t.conversions} prev={prevOk ? t.prev_conversions : undefined} period={period} />
      </div>
      <p className="muted small">GA4 counts sessions on this project&apos;s own hostnames only. The window ends on the latest collected day ({str(t.end_date) || "none yet"}).</p>

      <div className="grid-2">
        <LineChart title="Sessions per day" x={x} series={[{ label: "Sessions", slot: 1, values: s("sessions") }]} />
        <LineChart title="Engagement rate per day" x={x} format="pct" series={[{ label: "Engagement rate", slot: 1, values: rate }]} />
      </div>

      <Section title="Channels" note="Where sessions came from, by GA4's default channel grouping.">
        <div className="scroll"><table>
          <thead><tr><th>Channel</th><th className="num">Sessions</th><th className="num">Share</th><th className="num">Prev sessions</th><th className="num">Engaged</th><th className="num">Engagement rate</th><th className="num">Conversions</th></tr></thead>
          <tbody>
            {d.channels.map((c) => (
              <tr key={str(c.channel)}>
                <td>{str(c.channel)}</td>
                <td className="num">{fmtInt(c.sessions)}</td>
                <td className="num">{totalSessions ? fmtPct(Number(c.sessions) / totalSessions, 0) : "–"}</td>
                <td className="num muted">{fmtInt(c.prev_sessions)}</td>
                <td className="num">{fmtInt(c.engaged)}</td>
                <td className="num">{Number(c.sessions) > 0 ? fmtPct(Number(c.engaged) / Number(c.sessions), 0) : "–"}</td>
                <td className="num">{fmtInt(c.conversions)}</td>
              </tr>
            ))}
            {d.channels.length === 0 && <Empty cols={7}>No GA4 sessions in this range.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Landing pages">
        <div className="scroll"><table>
          <thead><tr><th>Landing page</th><th className="num">Sessions</th><th className="num">Engaged</th><th className="num">Engagement rate</th><th className="num">Conversions</th><th>Main channel</th></tr></thead>
          <tbody>
            {d.pages.map((p) => (
              <tr key={str(p.page_path)}>
                <td className="wrap-anywhere">{str(p.page_path) || "(not set)"}</td>
                <td className="num">{fmtInt(p.sessions)}</td><td className="num">{fmtInt(p.engaged)}</td>
                <td className="num">{Number(p.sessions) > 0 ? fmtPct(Number(p.engaged) / Number(p.sessions), 0) : "–"}</td>
                <td className="num">{fmtInt(p.conversions)}</td>
                <td className="muted">{str(p.top_channel)}</td>
              </tr>
            ))}
            {d.pages.length === 0 && <Empty cols={6}>No GA4 landing pages in this range.</Empty>}
          </tbody>
        </table></div>
      </Section>
    </>
  );
}
