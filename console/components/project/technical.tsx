import { Empty, Section, Severity, StatTile, Status } from "@/components/ui";
import { withProject } from "@/lib/db";
import { fmtDate, fmtDay, fmtInt, pathOf, str, truncate } from "@/lib/format";
import { LATEST_CRAWL } from "@/lib/sql";
import type { TabProps } from "./overview";

const NOINDEX = `(coalesce(robots_meta, '') ilike '%noindex%' or coalesce(x_robots_tag, '') ilike '%noindex%')`;
const INDEXABLE_HTML = `(status_code = 200 and word_count is not null and not ${NOINDEX})`;

type Violation = { rule_key?: string; url?: string; detail?: string; assertion?: string };

export async function TechnicalTab({ projectId }: TabProps) {
  const d = await withProject(projectId, async (q) => ({
    summary: (await q(`with ${LATEST_CRAWL}
      select (select max(collected_at) from crawl) as at, count(*)::int as pages,
        count(*) filter (where status_code between 200 and 299)::int as s2, count(*) filter (where status_code between 300 and 399)::int as s3,
        count(*) filter (where status_code between 400 and 499)::int as s4, count(*) filter (where status_code >= 500)::int as s5,
        count(*) filter (where ${INDEXABLE_HTML})::int as indexable_html,
        count(*) filter (where status_code = 200 and word_count is not null and ${NOINDEX})::int as noindex,
        count(*) filter (where ${INDEXABLE_HTML} and coalesce(title, '') = '')::int as no_title,
        count(*) filter (where ${INDEXABLE_HTML} and coalesce(meta_description, '') = '')::int as no_meta,
        count(*) filter (where ${INDEXABLE_HTML} and coalesce(array_length(h1, 1), 0) = 0)::int as no_h1,
        count(*) filter (where ${INDEXABLE_HTML} and coalesce(array_length(h1, 1), 0) > 1)::int as multi_h1,
        count(*) filter (where ${INDEXABLE_HTML} and word_count < 300)::int as thin,
        count(*) filter (where ${INDEXABLE_HTML} and not coalesce(in_sitemap, false))::int as not_in_sitemap
      from crawl`))[0] ?? {},
    pages: await q(`with ${LATEST_CRAWL}
      select url, status_code, title, meta_description, h1, word_count, canonical, robots_meta, x_robots_tag, in_sitemap, schema_types, internal_links_out
      from crawl order by (status_code >= 400) desc, url limit 400`),
    schema: await q(`with ${LATEST_CRAWL}
      select t as type, count(*)::int as pages from crawl, unnest(schema_types) t group by t order by pages desc`),
    sitemap: (await q(`with sm as (select run_id from raw_sitemap_urls where project_id = $1 order by collected_at desc limit 1)
      select count(distinct url)::int as urls, count(distinct sitemap_url)::int as sitemaps, max(collected_at) as at
      from raw_sitemap_urls where project_id = $1 and run_id = (select run_id from sm)`))[0] ?? {},
    inspection: await q(`select distinct on (url) url, indexable, robots_meta as robots_txt, canonical as google_canonical, collected_at
      from raw_crawl_pages where project_id = $1 and status_code is null and (indexable is not null or robots_meta is not null)
      order by url, collected_at desc`),
    probe: await q(`with pr as (select id, started_at, status from runs where project_id = $1 and workflow = 'daily_probe' order by started_at desc limit 1)
      select c.url, c.status_code, c.robots_meta, c.x_robots_tag, c.in_sitemap, c.canonical, pr.started_at, pr.status as run_status
      from raw_crawl_pages c join pr on c.run_id = pr.id where c.project_id = $1 order by c.url`),
    violations: await q(`select output->'violations' as v from agent_logs where project_id = $1 and agent = 'header_probe'
      and run_id = (select id from runs where project_id = $1 and workflow = 'daily_probe' order by started_at desc limit 1)`),
    rules: await q("select rule_key, url_pattern, assertion from critical_rules where project_id = $1 and active order by rule_key"),
    vitals: await q(`select distinct on (url, strategy, source) url, strategy, source, lcp_ms, inp_ms, cls, collected_at
      from raw_vitals where project_id = $1 order by url, strategy, source, collected_at desc`),
    issues: await q(`select id, severity, issue_type, url, evidence, recommended_fix, last_seen_at, seen_count from issues
      where project_id = $1 and status = 'open' order by array_position(array['critical','high','medium','low'], severity), last_seen_at desc limit 200`),
    issueCounts: (await q(`select count(*) filter (where status = 'open')::int as open, count(*) filter (where status = 'resolved')::int as resolved,
      count(*) filter (where status = 'dismissed')::int as dismissed from issues where project_id = $1`))[0] ?? {},
  }));
  const s = d.summary;
  const indexed = d.inspection.filter((r) => r.indexable === true).length;
  const notIndexed = d.inspection.filter((r) => r.indexable === false).length;
  const violations = ((d.violations[0]?.v as Violation[] | null) ?? []);
  const probeAt = d.probe[0]?.started_at;

  return (
    <>
      <div className="kpis">
        <StatTile label="Pages in the latest audit" value={fmtInt(s.pages)} foot={s.at ? `crawled ${fmtDate(s.at)}` : "no audit yet"} />
        <StatTile label="Status codes" value={`${fmtInt(s.s2)} ok`} foot={`${fmtInt(s.s3)} redirects · ${fmtInt(s.s4)} 4xx · ${fmtInt(s.s5)} 5xx`} />
        <StatTile label="Indexable HTML pages" value={fmtInt(s.indexable_html)} foot={`${fmtInt(s.noindex)} HTML pages marked noindex`} />
        <StatTile label="Indexed by Google" value={d.inspection.length ? `${fmtInt(indexed)} of ${fmtInt(d.inspection.length)}` : "–"}
          foot={d.inspection.length ? `${fmtInt(notIndexed)} not indexed, from URL inspection` : "no URL inspection yet"} />
        <StatTile label="Sitemap URLs" value={fmtInt(d.sitemap.urls)} foot={d.sitemap.at ? `${fmtInt(d.sitemap.sitemaps)} sitemap file(s), read ${fmtDay(d.sitemap.at)}` : "not read yet"} />
        <StatTile label="Protected-route violations" value={probeAt ? fmtInt(violations.length) : "–"}
          foot={probeAt ? `daily probe ${fmtDate(probeAt)}` : "no probe yet"} />
      </div>

      <Section title="On-page health" note="Counted over indexable HTML pages in the latest audit.">
        <div className="scroll"><table>
          <thead><tr><th>Check</th><th className="num">Pages</th></tr></thead>
          <tbody>
            {([
              ["Missing title", s.no_title], ["Missing meta description", s.no_meta], ["Missing H1", s.no_h1],
              ["More than one H1", s.multi_h1], ["Under 300 words", s.thin], ["Indexable but not in the sitemap", s.not_in_sitemap],
            ] as const).map(([k, v]) => (
              <tr key={k}><td>{k}</td><td className="num">{fmtInt(v)}</td></tr>
            ))}
          </tbody>
        </table></div>
      </Section>

      <Section title="Open issues" note={`${fmtInt(d.issueCounts.open)} open · ${fmtInt(d.issueCounts.resolved)} resolved because a later audit no longer saw them · ${fmtInt(d.issueCounts.dismissed)} dismissed`}>
        <div className="scroll"><table>
          <thead><tr><th>Severity</th><th>Type</th><th>URL</th><th>Evidence</th><th>Recommended fix</th><th>Seen</th></tr></thead>
          <tbody>
            {d.issues.map((i) => (
              <tr key={str(i.id)}>
                <td><Severity level={i.severity} /></td>
                <td>{str(i.issue_type).replaceAll("_", " ")}</td>
                <td className="wrap-anywhere">{i.url ? pathOf(i.url) : <em className="muted">protected route</em>}</td>
                <td className="small">{str(i.evidence)}</td>
                <td className="small">{str(i.recommended_fix)}</td>
                <td className="muted small nowrap">{fmtInt(i.seen_count)}x, last {fmtDay(i.last_seen_at)}</td>
              </tr>
            ))}
            {d.issues.length === 0 && <Empty cols={6}>No open issues.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Protected routes" note="Probed every morning. Each must stay out of the index and out of the sitemap.">
        {violations.length > 0 && (
          <div className="card alert">
            <strong>{violations.length} violation(s) in the latest probe.</strong>
            <ul>{violations.map((v, i) => <li key={i}>{str(v.rule_key)}: {pathOf(v.url)} {v.detail ? `(${str(v.detail)})` : ""}</li>)}</ul>
          </div>
        )}
        <div className="scroll"><table>
          <thead><tr><th>URL</th><th className="num">Status</th><th>Robots meta</th><th>X-Robots-Tag</th><th>In sitemap</th><th>Redirects to</th></tr></thead>
          <tbody>
            {d.probe.map((p) => (
              <tr key={str(p.url)}>
                <td className="wrap-anywhere">{pathOf(p.url)}</td>
                <td className="num">{str(p.status_code) || "–"}</td>
                <td className="small">{str(p.robots_meta) || <span className="muted">none</span>}</td>
                <td className="small">{str(p.x_robots_tag) || <span className="muted">none</span>}</td>
                <td>{p.in_sitemap ? <span className="pill critical">yes</span> : <span className="pill ok">no</span>}</td>
                <td className="small wrap-anywhere">{p.canonical ? pathOf(p.canonical) : ""}</td>
              </tr>
            ))}
            {d.probe.length === 0 && <Empty cols={6}>No probe yet.</Empty>}
          </tbody>
        </table></div>
        <details><summary className="muted small">Rules checked ({d.rules.length})</summary>
          <div className="scroll"><table>
            <thead><tr><th>Rule</th><th>URL pattern</th><th>Assertion</th></tr></thead>
            <tbody>{d.rules.map((r) => <tr key={str(r.rule_key)}><td>{str(r.rule_key)}</td><td><code>{str(r.url_pattern)}</code></td><td>{str(r.assertion)}</td></tr>)}</tbody>
          </table></div>
        </details>
      </Section>

      <Section title="Google index status" note="From Search Console URL inspection, latest verdict per URL.">
        <div className="scroll"><table>
          <thead><tr><th>URL</th><th>Indexed</th><th>robots.txt</th><th>Google-selected canonical</th><th>Inspected</th></tr></thead>
          <tbody>
            {d.inspection.map((r) => (
              <tr key={str(r.url)}>
                <td className="wrap-anywhere">{pathOf(r.url)}</td>
                <td>{r.indexable == null ? <span className="muted">unknown</span> : <Status value={r.indexable ? "indexed" : "no"} />}</td>
                <td className="small">{str(r.robots_txt).toLowerCase().replaceAll("_", " ")}</td>
                <td className="small wrap-anywhere">{r.google_canonical ? pathOf(r.google_canonical) : ""}</td>
                <td className="muted nowrap">{fmtDay(r.collected_at)}</td>
              </tr>
            ))}
            {d.inspection.length === 0 && <Empty cols={5}>No URL inspection results yet.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Core Web Vitals" note="Lab and field values from PageSpeed Insights. Good: LCP under 2,500 ms, INP under 200 ms, CLS under 0.1.">
        <div className="scroll"><table>
          <thead><tr><th>URL</th><th>Strategy</th><th>Source</th><th className="num">LCP ms</th><th className="num">INP ms</th><th className="num">CLS</th><th>Measured</th></tr></thead>
          <tbody>
            {d.vitals.map((v, i) => (
              <tr key={i}>
                <td className="wrap-anywhere">{pathOf(v.url)}</td><td>{str(v.strategy)}</td><td>{str(v.source)}</td>
                <td className="num">{fmtInt(v.lcp_ms)}</td><td className="num">{fmtInt(v.inp_ms)}</td>
                <td className="num">{v.cls == null ? "–" : Number(v.cls).toFixed(3)}</td>
                <td className="muted nowrap">{fmtDay(v.collected_at)}</td>
              </tr>
            ))}
            {d.vitals.length === 0 && <Empty cols={7}>No vitals yet. The monthly run measures them once a PageSpeed API key is set (PAGESPEED_API_KEY on the worker).</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Structured data" note="Schema.org types found on pages in the latest audit.">
        <div className="scroll"><table>
          <thead><tr><th>Type</th><th className="num">Pages</th></tr></thead>
          <tbody>
            {d.schema.map((r) => <tr key={str(r.type)}><td>{str(r.type)}</td><td className="num">{fmtInt(r.pages)}</td></tr>)}
            {d.schema.length === 0 && <Empty cols={2}>No structured data found.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Pages in the latest audit" note="Errors first, then by URL.">
        <div className="scroll"><table>
          <thead><tr><th>URL</th><th className="num">Status</th><th>Title</th><th className="num">Meta chars</th><th className="num">H1s</th><th className="num">Words</th><th className="num">Links out</th><th>Robots</th><th>Sitemap</th><th>Schema</th></tr></thead>
          <tbody>
            {d.pages.map((p) => {
              const title = str(p.title);
              const robots = [str(p.robots_meta), str(p.x_robots_tag)].filter(Boolean).join(", ");
              return (
                <tr key={str(p.url)}>
                  <td className="wrap-anywhere">{pathOf(p.url)}</td>
                  <td className="num">{str(p.status_code)}</td>
                  <td className="small">{title ? <>{truncate(title, 70)} <span className="muted">({title.length})</span></> : <span className="muted">none</span>}</td>
                  <td className="num">{p.meta_description ? str(p.meta_description).length : "–"}</td>
                  <td className="num">{Array.isArray(p.h1) ? (p.h1 as unknown[]).length : "–"}</td>
                  <td className="num">{fmtInt(p.word_count)}</td>
                  <td className="num">{fmtInt(p.internal_links_out)}</td>
                  <td className="small">{robots || <span className="muted">none</span>}</td>
                  <td>{p.in_sitemap ? "yes" : <span className="muted">no</span>}</td>
                  <td className="small muted">{Array.isArray(p.schema_types) ? (p.schema_types as string[]).join(", ") : ""}</td>
                </tr>
              );
            })}
            {d.pages.length === 0 && <Empty cols={10}>No audit yet.</Empty>}
          </tbody>
        </table></div>
      </Section>
    </>
  );
}
