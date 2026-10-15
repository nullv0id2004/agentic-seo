import Link from "next/link";
import { ColumnChart, LineChart } from "@/components/charts";
import { Empty, Section, Status, StatTile } from "@/components/ui";
import { withProject, type Row } from "@/lib/db";
import { fmtCompact, fmtDate, fmtDay, fmtInt, fmtPos, fmtUsd, num, str } from "@/lib/format";
import { GA4_DAILY, GA4_TOTALS, GSC_DAILY, GSC_TOTALS, ourRank, projectDomains } from "@/lib/sql";

export interface TabProps { projectId: string; slug: string; range: number; project: Row }

const series = (rows: Row[], key: string) => rows.map((r) => (r.collected ? num(r[key]) : null));

export async function OverviewTab({ projectId, slug, range, project }: TabProps) {
  const d = await withProject(projectId, async (q) => ({
    gscDaily: await q(GSC_DAILY, [range]),
    gsc: (await q(GSC_TOTALS, [range]))[0] ?? {},
    gaDaily: await q(GA4_DAILY, [range]),
    ga: (await q(GA4_TOTALS, [range]))[0] ?? {},
    issues: (await q(`select
        count(*) filter (where severity = 'critical')::int as critical, count(*) filter (where severity = 'high')::int as high,
        count(*) filter (where severity = 'medium')::int as medium, count(*) filter (where severity = 'low')::int as low
      from issues where project_id = $1 and status = 'open'`))[0] ?? {},
    approvals: (await q(`select count(*)::int as pending, count(*) filter (where severity = 'critical')::int as critical
      from approvals where project_id = $1 and status = 'pending'`))[0] ?? {},
    spend: (await q(`select coalesce(sum(cost_usd), 0) as month,
        coalesce(sum(cost_usd) filter (where started_at >= date_trunc('month', now()) - interval '1 month' and started_at < date_trunc('month', now())), 0) as last_month
      from runs where project_id = $1 and started_at >= date_trunc('month', now()) - interval '1 month'`))[0] ?? {},
    ai: (await q(`with latest as (select run_id from raw_llm_responses where project_id = $1 order by collected_at desc limit 1)
      select count(*)::int as asked, count(*) filter (where cites_project)::int as citing, max(collected_at) as at
      from raw_llm_responses where project_id = $1 and run_id = (select run_id from latest)`))[0] ?? {},
    health: await q(`select distinct on (workflow) workflow, trigger, status, started_at, ended_at, cost_usd, halt_reason
      from runs where project_id = $1 order by workflow, started_at desc`),
    freshness: (await q(`select
        (select max(date) from raw_gsc_performance where project_id = $1) as gsc,
        (select max(date) from raw_ga4_daily where project_id = $1) as ga4,
        (select max(collected_at) from raw_crawl_pages where project_id = $1 and word_count is not null) as crawl,
        (select max(collected_at) from raw_crawl_pages where project_id = $1 and status_code is null and indexable is not null) as inspection,
        (select max(collected_at) from raw_serp where project_id = $1) as serp,
        (select max(collected_at) from raw_keyword_metrics where project_id = $1) as keywords,
        (select max(collected_at) from raw_llm_responses where project_id = $1) as ai,
        (select max(collected_at) from raw_vitals where project_id = $1) as vitals`))[0] ?? {},
    events: await q("select kind, name, source_url, observed_on from trend_events where project_id = $1 order by observed_on desc, last_seen_at desc limit 8"),
    gaps: (await q("select count(*)::int as n from collection_gaps where project_id = $1 and created_at >= now() - interval '7 days'"))[0] ?? {},
    positions: (await q(`with sp as (select distinct on (lower(query)) lower(query) as k, results from raw_serp
          where project_id = $1 order by lower(query), collected_at desc),
        ranks as (select ${ourRank("sp.results", "$2")} as r from sp)
      select count(*) filter (where r between 1 and 3)::int as top3, count(*) filter (where r between 4 and 10)::int as top10,
        count(*) filter (where r between 11 and 20)::int as top20, count(*) filter (where r is null or r > 20)::int as beyond,
        count(*)::int as tracked from ranks`, [projectDomains(project)]))[0] ?? {},
    gap: (await q(`with lr as (select run_id from raw_keyword_ideas where project_id = $1 and source = 'competitor' order by collected_at desc limit 1),
        orun as (select run_id from raw_keyword_ideas where project_id = $1 and source = 'own' order by collected_at desc limit 1),
        comp as (select distinct keyword from raw_keyword_ideas where project_id = $1 and run_id = (select run_id from lr) and source = 'competitor'),
        mine as (select distinct keyword from raw_keyword_ideas where project_id = $1 and run_id = (select run_id from orun) and source = 'own'),
        rd as (select run_id from raw_referring_domains where project_id = $1 order by collected_at desc limit 1)
      select (select count(*) from comp)::int as competitor_keywords, (select count(*) from comp where keyword not in (select keyword from mine))::int as not_ranking,
        (select count(distinct domain) from raw_referring_domains where project_id = $1 and run_id = (select run_id from rd) and role = 'own')::int as own_domains,
        (select max(collected_at) from raw_referring_domains where project_id = $1) as links_at,
        (select max(collected_at) from raw_keyword_ideas where project_id = $1 and run_id = (select run_id from lr)) as keywords_at`))[0] ?? {},
  }));

  const gsc = d.gsc;
  const ga = d.ga;
  const period = `previous ${range} days`;
  const gscPrev = Number(gsc.prev_days) > 0;
  const gaPrev = Number(ga.prev_days) > 0;
  const cap = Number(project.monthly_cost_cap_usd ?? 0);
  const x = d.gscDaily.map((r) => str(r.date));
  const gx = d.gaDaily.map((r) => str(r.date));
  const openTotal = ["critical", "high", "medium", "low"].reduce((a, k) => a + Number(d.issues[k] ?? 0), 0);

  return (
    <>
      <div className="kpis cols-4">
        <StatTile label="Search clicks" value={fmtCompact(gsc.clicks)} current={gsc.clicks} prev={gscPrev ? gsc.prev_clicks : undefined}
          period={period} trend={series(d.gscDaily, "clicks")} />
        <StatTile label="Search impressions" value={fmtCompact(gsc.impressions)} current={gsc.impressions} prev={gscPrev ? gsc.prev_impressions : undefined}
          period={period} trend={series(d.gscDaily, "impressions")} />
        <StatTile label="Average position" value={fmtPos(gsc.position)} current={gsc.position} prev={gscPrev ? gsc.prev_position : undefined}
          period={period} upIsGood={false} trend={series(d.gscDaily, "position")} />
        <StatTile label="Sessions (GA4)" value={fmtCompact(ga.sessions)} current={ga.sessions} prev={gaPrev ? ga.prev_sessions : undefined}
          period={period} trend={series(d.gaDaily, "sessions")} />
        <StatTile label="Open issues" value={fmtInt(openTotal)}
          foot={<>{fmtInt(d.issues.critical)} critical · {fmtInt(d.issues.high)} high · {fmtInt(d.issues.medium)} medium</>} />
        <StatTile label="Pending approvals" value={fmtInt(d.approvals.pending)}
          foot={<Link href={`/projects/${slug}/approvals`}>{Number(d.approvals.critical) > 0 ? `${fmtInt(d.approvals.critical)} critical, open the queue` : "Open the queue"}</Link>} />
        <StatTile label="AI prompts citing the site" value={d.ai.asked ? `${fmtInt(d.ai.citing)} of ${fmtInt(d.ai.asked)}` : "–"}
          foot={d.ai.at ? `latest weekly check ${fmtDay(d.ai.at)}` : "no weekly AI check yet"} />
        <StatTile label="Spend this month" value={fmtUsd(d.spend.month)}
          foot={<>{cap ? `${((Number(d.spend.month) / cap) * 100).toFixed(0)}% of ${fmtUsd(cap, 0)} cap` : ""} · last month {fmtUsd(d.spend.last_month)}</>} />
      </div>
      <p className="muted small">
        Search Console data is final about three days after the day; the window ends on the latest collected day ({str(gsc.end_date) || "none yet"}).
        GA4 ends on {str(ga.end_date) || "none yet"}. Days with no collection show as gaps, not zeros.
      </p>

      <div className="grid-2">
        <ColumnChart title="Where your tracked keywords rank on Google" note={`${fmtInt(d.positions.tracked)} keywords, latest weekly check`} xLabel="Position"
          x={["Top 3", "4 to 10", "11 to 20", "Not in top 20"]}
          series={[{ label: "Keywords", slot: 1, values: [num(d.positions.top3), num(d.positions.top10), num(d.positions.top20), num(d.positions.beyond)] }]} />
        <Section title="Competitive snapshot" note="Where competitors win and you don't. The full comparison is under Competitive research.">
          <div className="kpis cols-3">
            <StatTile label="Keywords competitors rank for" value={fmtInt(d.gap.competitor_keywords)}
              foot={d.gap.keywords_at ? `in your topic, ${fmtDay(d.gap.keywords_at)}` : "collected by the monthly run"} />
            <StatTile label="…that you don't rank for" value={fmtInt(d.gap.not_ranking)}
              foot={<Link href={`/projects/${slug}?tab=competitors&range=${range}`}>Open the keyword gap</Link>} />
            <StatTile label="Your referring domains" value={d.gap.links_at ? fmtInt(d.gap.own_domains) : "–"}
              foot={<Link href={`/projects/${slug}?tab=competitors&range=${range}`}>Open the backlink gap</Link>} />
          </div>
        </Section>
      </div>

      <div className="grid-2">
        <LineChart title="Search clicks per day" x={x} series={[{ label: "Clicks", slot: 1, values: series(d.gscDaily, "clicks") }]} />
        <LineChart title="Search impressions per day" x={x} series={[{ label: "Impressions", slot: 1, values: series(d.gscDaily, "impressions") }]} />
        <LineChart title="Sessions per day (GA4)" x={gx} series={[{ label: "Sessions", slot: 1, values: series(d.gaDaily, "sessions") }]} />
        <LineChart title="Average position per day" note="lower is better" x={x} invert format="pos"
          series={[{ label: "Position", slot: 1, values: series(d.gscDaily, "position") }]} />
      </div>

      <div className="grid-2">
        <Section title="Pipeline health" note="The most recent run of each workflow.">
          <div className="scroll"><table>
            <thead><tr><th>Workflow</th><th>Status</th><th>Started</th><th className="num">Cost</th></tr></thead>
            <tbody>
              {d.health.map((r) => (
                <tr key={str(r.workflow)}>
                  <td>{str(r.workflow).replaceAll("_", " ")} <span className="muted small">{str(r.trigger)}</span></td>
                  <td><Status value={r.status} />{r.halt_reason ? <div className="muted small">{str(r.halt_reason)}</div> : null}</td>
                  <td className="nowrap">{fmtDate(r.started_at)}</td>
                  <td className="num">{fmtUsd(r.cost_usd, 3)}</td>
                </tr>
              ))}
              {d.health.length === 0 && <Empty cols={4}>no runs yet</Empty>}
            </tbody>
          </table></div>
          <p className="muted small">{fmtInt(d.gaps.n)} collection gap(s) in the last 7 days. <Link href={`/projects/${slug}?tab=operations&range=${range}`}>See operations</Link></p>
        </Section>

        <Section title="Data freshness" note="When each source last delivered data.">
          <div className="scroll"><table>
            <thead><tr><th>Source</th><th>Latest</th><th>Cadence</th></tr></thead>
            <tbody>
              {([
                ["Search Console performance", fmtDay(d.freshness.gsc), "daily, 3 days behind"],
                ["GA4", fmtDay(d.freshness.ga4), "daily, 1 day behind"],
                ["Site audit crawl", fmtDate(d.freshness.crawl), "after each deploy and monthly"],
                ["Google URL inspection", fmtDate(d.freshness.inspection), "weekly"],
                ["SERP rankings", fmtDate(d.freshness.serp), "weekly"],
                ["AI visibility", fmtDate(d.freshness.ai), "weekly"],
                ["Keyword metrics", fmtDate(d.freshness.keywords), "quarterly"],
                ["Core Web Vitals", fmtDate(d.freshness.vitals), "monthly, needs a PageSpeed key"],
              ] as const).map(([k, v, c]) => (
                <tr key={k}><td>{k}</td><td className="nowrap">{v || <span className="muted">never</span>}</td><td className="muted small">{c}</td></tr>
              ))}
            </tbody>
          </table></div>
        </Section>
      </div>

      <Section title="Latest monitoring events">
        <div className="scroll"><table>
          <thead><tr><th>Date</th><th>Kind</th><th>Event</th></tr></thead>
          <tbody>
            {d.events.map((t, i) => (
              <tr key={i}>
                <td className="nowrap">{fmtDay(t.observed_on)}</td><td>{str(t.kind).replaceAll("_", " ")}</td>
                <td><a href={str(t.source_url)} rel="noreferrer" target="_blank">{str(t.name)}</a></td>
              </tr>
            ))}
            {d.events.length === 0 && <Empty cols={3}>none</Empty>}
          </tbody>
        </table></div>
      </Section>
    </>
  );
}
