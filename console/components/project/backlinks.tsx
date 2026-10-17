import { LineChart } from "@/components/charts";
import { DataTable, type DRow } from "@/components/datatable";
import { Section, StatTile } from "@/components/ui";
import { withProject, type Row } from "@/lib/db";
import { fmtCompact, fmtDay, fmtInt, num, str } from "@/lib/format";
import type { TabProps } from "./overview";

const SPAM_CHECK = 30;    // DataForSEO spam score worth a look
const SPAM_TOXIC = 60;    // likely toxic

function monthLabel(m: string): string {
  return new Date(`${m}T00:00:00Z`).toLocaleString("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" });
}

/** Backlinks: the site's linking domains, what was gained and lost, spam scores, and how fast competitors gain links. */
export async function BacklinksTab({ projectId, project }: TabProps) {
  const d = await withProject(projectId, async (q) => ({
    summary: (await q(`select rank, backlinks, referring_domains, collected_at from raw_backlink_summary
      where project_id = $1 and role = 'own' order by collected_at desc limit 1`))[0],
    domains: await q(`with lr as (select run_id from raw_referring_domains where project_id = $1 and role = 'own' order by collected_at desc limit 1)
      select domain, rank, backlinks, spam_score, first_seen, lost_date from raw_referring_domains
      where project_id = $1 and role = 'own' and run_id = (select run_id from lr) order by rank desc nulls last, domain`),
    changes: await q(`with lr as (select run_id from raw_backlink_changes where project_id = $1 order by collected_at desc limit 1)
      select target, role, month::text as month, new_backlinks, lost_backlinks, new_referring_domains, lost_referring_domains
      from raw_backlink_changes where project_id = $1 and run_id = (select run_id from lr) order by month`),
    collected: (await q("select max(collected_at) as at from raw_referring_domains where project_id = $1 and role = 'own'"))[0]?.at,
  }));

  const own = str((project.domains as string[])[0]).replace(/^www\./, "");
  const now = Date.now();
  const days = (v: unknown) => (v ? (now - new Date(str(v)).getTime()) / 86_400_000 : null);
  const live = d.domains.filter((r) => !r.lost_date);
  const isNew = (r: Row) => !r.lost_date && (days(r.first_seen) ?? 1e9) <= 90;
  const isLost = (r: Row) => !!r.lost_date && (days(r.lost_date) ?? 1e9) <= 90;
  const spammy = live.filter((r) => (num(r.spam_score) ?? 0) > SPAM_CHECK);
  const new30 = live.filter((r) => (days(r.first_seen) ?? 1e9) <= 30).length;
  const lost30 = d.domains.filter((r) => r.lost_date && (days(r.lost_date) ?? 1e9) <= 30).length;

  const ownChanges = d.changes.filter((c) => c.role === "own");
  const months = Array.from(new Set(d.changes.map((c) => str(c.month)))).sort();
  const byMonth = new Map(ownChanges.map((c) => [str(c.month), c]));

  // competitors' link velocity: the last three full months and the year
  const recent = months.slice(-4, -1);
  const velocity = Array.from(new Set(d.changes.map((c) => str(c.target)))).map((t) => {
    const rows = d.changes.filter((c) => c.target === t);
    const sum = (k: string, ms?: string[]) => rows.filter((c) => !ms || ms.includes(str(c.month))).reduce((a, c) => a + (num(c[k]) ?? 0), 0);
    return { target: t, own: rows[0]?.role === "own", gained3: sum("new_referring_domains", recent), lost3: sum("lost_referring_domains", recent),
             gained12: sum("new_referring_domains"), lost12: sum("lost_referring_domains") };
  }).sort((a, b) => (b.gained3 - b.lost3) - (a.gained3 - a.lost3));

  const linkRows: DRow[] = d.domains.map((r) => {
    const status = isLost(r) ? "lost" : isNew(r) ? "new" : r.lost_date ? "lost earlier" : "live";
    const spam = num(r.spam_score);
    return {
      key: str(r.domain),
      facet: status === "lost earlier" ? "lost" : status,
      cells: [
        <a key="d" href={`https://${str(r.domain)}/`} target="_blank" rel="noreferrer nofollow">{str(r.domain)}</a>,
        <span key="s" className={`pill ${status === "new" ? "ok" : status.startsWith("lost") ? "critical" : "plain"}`}>{status}</span>,
        fmtInt(r.rank), fmtInt(r.backlinks),
        spam == null ? <span className="muted">–</span> : spam > SPAM_TOXIC ? <span className="pill critical">{spam} likely toxic</span>
          : spam > SPAM_CHECK ? <span className="pill warning">{spam} check</span> : fmtInt(spam),
        <span key="f" className="nowrap muted">{fmtDay(r.first_seen)}</span>, <span key="l" className="nowrap muted">{fmtDay(r.lost_date)}</span>,
      ],
      values: [str(r.domain), status, num(r.rank), num(r.backlinks), spam, fmtDay(r.first_seen), fmtDay(r.lost_date)],
    };
  });

  return (
    <>
      <div className="kpis cols-4">
        <StatTile label="Referring domains" value={fmtInt(live.length)} foot={d.summary ? `${fmtCompact(d.summary.backlinks)} backlinks, domain rank ${fmtInt(d.summary.rank)}` : "no backlink collection yet"} />
        <StatTile label="New in the last 30 days" value={fmtInt(new30)} foot="by the date DataForSEO first saw the link" />
        <StatTile label="Lost in the last 30 days" value={fmtInt(lost30)} foot={lost30 ? "see Lost below" : "none lost"} />
        <StatTile label={`Spam score above ${SPAM_CHECK}`} value={fmtInt(spammy.length)} foot={spammy.length ? "check these before building more links" : "nothing suspicious"} />
      </div>

      {d.domains.length === 0 ? (
        <div className="card"><strong>No links to {own} on record yet.</strong> DataForSEO has found no site linking to it. The Link building page lists where to start; new links show up here as they are found.</div>
      ) : null}

      <Section title="New and lost per month" note={`Referring domains ${own} gained and lost each month, as DataForSEO records them.`}>
          {ownChanges.length ? (
            <LineChart title="Referring domains gained and lost" x={months.map(monthLabel)} xLabel="Month"
              series={[{ label: "Gained", slot: 1, values: months.map((m) => num(byMonth.get(m)?.new_referring_domains) ?? 0) },
                       { label: "Lost", slot: 2, values: months.map((m) => num(byMonth.get(m)?.lost_referring_domains) ?? 0) }]} />
          ) : <p className="muted">No monthly changes collected yet.</p>}
      </Section>

      <Section title="Link velocity: you and competitors" note={`Referring domains gained and lost in the last three full months${recent.length ? ` (${monthLabel(recent[0])} to ${monthLabel(recent[recent.length - 1])})` : ""} and the last twelve. Competitors gaining fast are building links now: their new links are the freshest prospects.`}>
          <div className="scroll"><table>
            <thead><tr><th>Domain</th><th className="num">Gained, 3 mo</th><th className="num">Lost, 3 mo</th><th className="num">Net, 3 mo</th><th className="num">Gained, 12 mo</th><th className="num">Lost, 12 mo</th></tr></thead>
            <tbody>
              {velocity.map((v) => (
                <tr key={v.target}>
                  <td>{v.own ? <strong>{v.target}</strong> : v.target}{v.own ? <span className="muted small"> (you)</span> : null}</td>
                  <td className="num">{fmtInt(v.gained3)}</td><td className="num">{fmtInt(v.lost3)}</td>
                  <td className="num">{v.gained3 - v.lost3 > 0 ? "+" : ""}{fmtInt(v.gained3 - v.lost3)}</td>
                  <td className="num">{fmtInt(v.gained12)}</td><td className="num">{fmtInt(v.lost12)}</td>
                </tr>
              ))}
              {velocity.length === 0 ? <tr><td colSpan={6} className="muted">No monthly changes collected yet.</td></tr> : null}
            </tbody>
          </table></div>
      </Section>

      <Section title="Linking domains" note={`Every domain linking to ${own}: new (first seen in the last 90 days), live, or lost (with the date it was lost). Spam score is DataForSEO's 0 to 100; above ${SPAM_CHECK} is worth checking, above ${SPAM_TOXIC} is likely toxic. Collected ${fmtDay(d.collected) || "never"}.`}>
        <DataTable columns={[{ label: "Domain" }, { label: "Status" }, { label: "Domain rank", num: true }, { label: "Backlinks", num: true },
          { label: "Spam score", num: true }, { label: "First seen" }, { label: "Lost on" }]}
          rows={linkRows} facets={[{ key: "new", label: "New" }, { key: "live", label: "Live" }, { key: "lost", label: "Lost" }]}
          filename="linking-domains" searchLabel="Filter domains" empty="No linking domains on record yet." />
      </Section>
    </>
  );
}
