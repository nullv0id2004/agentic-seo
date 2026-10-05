import Link from "next/link";
import { Empty, Section, StatTile, Status } from "@/components/ui";
import { withProject } from "@/lib/db";
import { fmtDay, fmtInt, pathOf, str, truncate } from "@/lib/format";
import type { TabProps } from "./overview";

export async function ContentTab({ projectId, slug, project }: TabProps) {
  const d = await withProject(projectId, async (q) => ({
    suggestions: await q(`select url, field, current, suggested, rationale, status, created_at from onpage_suggestions
      where project_id = $1 order by (status = 'proposed') desc, created_at desc limit 100`),
    briefs: await q(`select b.title, b.status, b.gate_result, b.published_url, b.created_at, k.keyword, b.answer_block
      from content_briefs b left join keywords k on k.id = b.keyword_id and k.project_id = b.project_id
      where b.project_id = $1 order by b.created_at desc limit 50`),
    briefsThisMonth: (await q(`select count(*)::int as n from content_briefs where project_id = $1 and created_at >= date_trunc('month', now())`))[0] ?? {},
    pitches: await q("select outlet_url, contact_hint, subject, body, status, created_at from pitches where project_id = $1 order by created_at desc limit 50"),
    mentions: await q(`select source_url, target_url, kind, domain_rating, first_seen from mentions where project_id = $1
      order by first_seen desc nulls last, domain_rating desc nulls last limit 200`),
    mentionCounts: (await q(`select count(*) filter (where kind = 'link')::int as links, count(distinct split_part(split_part(source_url, '//', 2), '/', 1)) filter (where kind = 'link')::int as domains,
      count(*) filter (where kind <> 'link')::int as other from mentions where project_id = $1`))[0] ?? {},
  }));
  const cap = Number(project.monthly_content_cap ?? 0);
  const proposed = d.suggestions.filter((s) => s.status === "proposed").length;

  return (
    <>
      <div className="kpis">
        <StatTile label="On-page suggestions waiting" value={fmtInt(proposed)} foot={`${fmtInt(d.suggestions.length)} in total`} />
        <StatTile label="Content briefs this month" value={`${fmtInt(d.briefsThisMonth.n)} of ${fmtInt(cap)}`} foot="the monthly content cap is deliberately low" />
        <StatTile label="Outreach pitches" value={fmtInt(d.pitches.length)} foot="drafts; nothing is sent without approval" />
        <StatTile label="Backlinks" value={fmtInt(d.mentionCounts.links)} foot={`from ${fmtInt(d.mentionCounts.domains)} referring domain(s)`} />
      </div>

      <p className="small secondary">Approvals and what each one changed are on the <Link href={`/projects/${slug}?tab=changes`}>Changes</Link> tab.</p>

      <Section title="On-page suggestions">
        <div className="scroll"><table>
          <thead><tr><th>Page</th><th>Field</th><th>Current</th><th>Suggested</th><th>Why</th><th>Status</th></tr></thead>
          <tbody>
            {d.suggestions.map((x, i) => (
              <tr key={i}>
                <td className="wrap-anywhere">{pathOf(x.url)}</td><td>{str(x.field)}</td>
                <td className="muted small">{str(x.current)}</td><td className="small">{str(x.suggested)}</td>
                <td className="small">{str(x.rationale)}</td><td><Status value={x.status} /></td>
              </tr>
            ))}
            {d.suggestions.length === 0 && <Empty cols={6}>No suggestions yet.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Content briefs">
        <div className="scroll"><table>
          <thead><tr><th>Title</th><th>Keyword</th><th>Status</th><th>Gate</th><th>Published</th><th>Created</th></tr></thead>
          <tbody>
            {d.briefs.map((b, i) => (
              <tr key={i}>
                <td>{b.answer_block ? <details><summary>{str(b.title)}</summary><p className="small">{str(b.answer_block)}</p></details> : str(b.title)}</td>
                <td className="muted">{str(b.keyword)}</td><td><Status value={b.status} /></td><td className="muted">{str(b.gate_result)}</td>
                <td className="wrap-anywhere">{b.published_url ? <a href={str(b.published_url)} target="_blank" rel="noreferrer">{pathOf(b.published_url)}</a> : ""}</td>
                <td className="nowrap muted">{fmtDay(b.created_at)}</td>
              </tr>
            ))}
            {d.briefs.length === 0 && <Empty cols={6}>No briefs yet.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Outreach pitches" note="Drafts only. The worker never sends one without an approved approval row.">
        <div className="scroll"><table>
          <thead><tr><th>Outlet</th><th>Subject</th><th>Status</th><th>Drafted</th></tr></thead>
          <tbody>
            {d.pitches.map((p, i) => (
              <tr key={i}>
                <td className="wrap-anywhere"><a href={str(p.outlet_url)} target="_blank" rel="noreferrer">{str(p.outlet_url).replace(/^https?:\/\//, "")}</a>{p.contact_hint ? <div className="muted small">{str(p.contact_hint)}</div> : null}</td>
                <td><details><summary>{str(p.subject)}</summary><pre className="small">{truncate(p.body, 3000)}</pre></details></td>
                <td><Status value={p.status} /></td>
                <td className="nowrap muted">{fmtDay(p.created_at)}</td>
              </tr>
            ))}
            {d.pitches.length === 0 && <Empty cols={4}>No pitches yet.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Backlinks and mentions" note="Referring pages from the monthly backlink check, newest first.">
        <div className="scroll"><table>
          <thead><tr><th>Source</th><th>Points to</th><th>Kind</th><th className="num">Domain rating</th><th>First seen</th></tr></thead>
          <tbody>
            {d.mentions.map((m, i) => (
              <tr key={i}>
                <td className="wrap-anywhere"><a href={str(m.source_url)} target="_blank" rel="noreferrer">{str(m.source_url).replace(/^https?:\/\//, "")}</a></td>
                <td className="wrap-anywhere small">{m.target_url ? pathOf(m.target_url) : ""}</td>
                <td>{str(m.kind)}</td><td className="num">{fmtInt(m.domain_rating)}</td><td className="nowrap muted">{fmtDay(m.first_seen)}</td>
              </tr>
            ))}
            {d.mentions.length === 0 && <Empty cols={5}>No backlinks recorded yet. The monthly run checks them.</Empty>}
          </tbody>
        </table></div>
      </Section>
    </>
  );
}
