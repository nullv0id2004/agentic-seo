import { LineChart } from "@/components/charts";
import { Empty, Section, StatTile } from "@/components/ui";
import { withProject, type Row } from "@/lib/db";
import { fmtDay, fmtInt, fmtUsd, str, truncate } from "@/lib/format";
import type { TabProps } from "./overview";

type Citation = { url?: string; title?: string };

function host(u: unknown): string {
  return str(u).replace(/^https?:\/\//, "").split("/")[0].replace(/^www\./, "");
}

function MentionTable({ rows, empty }: { rows: Row[]; empty: string }) {
  return (
    <div className="scroll"><table>
      <thead><tr><th>Question</th><th>Found by</th><th>About this site</th><th>Platform</th><th className="num">AI volume</th></tr></thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td><details><summary>{str(r.question)}</summary><pre className="small">{truncate(r.answer, 2500)}</pre></details></td>
            <td>{str(r.target_kind)}</td>
            <td><span className={`pill ${r.about_project ? "ok" : ""}`}>{r.about_project ? (r.cites_project ? "yes, cites it" : "yes") : "no, another brand"}</span></td>
            <td className="muted">{str(r.platform)}</td>
            <td className="num">{fmtInt(r.ai_search_volume)}</td>
          </tr>
        ))}
        {rows.length === 0 && empty ? <Empty cols={5}>{empty}</Empty> : null}
      </tbody>
    </table></div>
  );
}

export async function AiTab({ projectId, range }: TabProps) {
  const d = await withProject(projectId, async (q) => ({
    history: await q(`select run_id, min(collected_at)::date::text as day, count(*)::int as asked,
        count(*) filter (where cites_project)::int as citing, coalesce(sum(cost_usd), 0) as cost
      from raw_llm_responses where project_id = $1 and collected_at >= now() - make_interval(days => $2::int)
      group by run_id order by min(collected_at)`, [range]),
    prompts: await q(`with lr as (select run_id from raw_llm_responses where project_id = $1 order by collected_at desc limit 1)
      select cur.platform, cur.model_name, cur.prompt, cur.cites_project, cur.citations, cur.response, cur.fan_out_queries, cur.web_search,
        cur.cost_usd, cur.collected_at,
        (select p.cites_project from raw_llm_responses p
          where p.project_id = $1 and p.run_id <> cur.run_id and p.prompt = cur.prompt and p.platform = cur.platform and p.collected_at < cur.collected_at
          order by p.collected_at desc limit 1) as prev_cites
      from raw_llm_responses cur where cur.project_id = $1 and cur.run_id = (select run_id from lr)
      order by cur.cites_project desc, cur.prompt`),
    domain: await q(`select platform, target, mentions, ai_search_volume, collected_at from raw_llm_mention_metrics
      where project_id = $1 and target_kind = 'domain'
        and run_id = (select run_id from raw_llm_mention_metrics where project_id = $1 and target_kind = 'domain' order by collected_at desc limit 1)
      order by platform`),
    brand: await q(`select target_kind, platform, question, answer, about_project, cites_project, ai_search_volume, collected_at
      from raw_llm_mentions where project_id = $1
        and run_id = (select run_id from raw_llm_mentions where project_id = $1 order by collected_at desc limit 1)
      order by about_project desc nulls last, cites_project desc, ai_search_volume desc nulls last limit 60`),
    aiKeywords: await q(`select distinct on (lower(keyword)) keyword, ai_search_volume, monthly, collected_at
      from raw_ai_keyword_metrics where project_id = $1 order by lower(keyword), collected_at desc`),
  }));

  const latest = d.history[d.history.length - 1];
  const domainMentions = d.domain.reduce((a, r) => a + Number(r.mentions ?? 0), 0);
  const brandRows = d.brand.filter((r) => r.target_kind === "brand");
  const ours = brandRows.filter((r) => r.about_project);
  const domainRows = d.brand.filter((r) => r.target_kind === "domain");
  const aiVolume = d.aiKeywords.reduce((a, r) => a + Number(r.ai_search_volume ?? 0), 0);

  return (
    <>
      <div className="kpis">
        <StatTile label="Prompts citing the site" value={latest ? `${fmtInt(latest.citing)} of ${fmtInt(latest.asked)}` : "–"}
          foot={latest ? `ChatGPT with web search, ${str(latest.day)}` : "no weekly check yet"} />
        <StatTile label="Domain mentions in AI answers" value={fmtInt(domainMentions)}
          foot={d.domain.length ? `across ${d.domain.length} platform(s)` : "the site is not in DataForSEO's AI mention index yet"} />
        <StatTile label="Brand answers about this site" value={brandRows.length ? `${fmtInt(ours.length)} of ${fmtInt(brandRows.length)}` : "–"}
          foot="other brands share the name; only answers naming the site or its context count" />
        <StatTile label="AI search volume, tracked keywords" value={fmtInt(aiVolume)}
          foot={d.aiKeywords.length ? `${d.aiKeywords.length} keywords, quarterly` : "measured by the quarterly keyword run"} />
      </div>

      <LineChart title="Weekly AI check" note={`last ${range} days`} xLabel="Check" x={d.history.map((h) => str(h.day))}
        series={[{ label: "Prompts asked", slot: 1, values: d.history.map((h) => Number(h.asked)) },
                 { label: "Prompts citing the site", slot: 2, values: d.history.map((h) => Number(h.citing)) }]} />

      <Section title="Prompts asked this week" note="The same questions are asked every week so answers stay comparable. Open a row to read the answer and its sources.">
        <div className="scroll"><table>
          <thead><tr><th>Prompt</th><th>Cites the site</th><th>Last week</th><th>Sources cited</th><th className="num">Cost</th></tr></thead>
          <tbody>
            {d.prompts.map((p, i) => {
              const cites = (p.citations as Citation[] | null) ?? [];
              const hosts = Array.from(new Set(cites.map((c) => host(c.url)).filter(Boolean)));
              return (
                <tr key={i}>
                  <td>
                    <details>
                      <summary>{str(p.prompt)}</summary>
                      <p className="muted small">{str(p.platform)} · {str(p.model_name)} · web search {p.web_search ? "used" : "not used"} · {fmtDay(p.collected_at)}</p>
                      <pre className="small">{truncate(p.response, 4000) || "No answer text."}</pre>
                      {cites.length > 0 && (
                        <ol className="small">
                          {cites.slice(0, 20).map((c, k) => <li key={k} className="wrap-anywhere"><a href={str(c.url)} target="_blank" rel="noreferrer">{str(c.title) || host(c.url)}</a> <span className="muted">{host(c.url)}</span></li>)}
                        </ol>
                      )}
                      {Array.isArray(p.fan_out_queries) && p.fan_out_queries.length > 0 && (
                        <p className="small muted">Searches the assistant ran: {(p.fan_out_queries as string[]).join(" · ")}</p>
                      )}
                    </details>
                  </td>
                  <td><span className={`pill ${p.cites_project ? "ok" : ""}`}>{p.cites_project ? "yes" : "no"}</span></td>
                  <td className="muted">{p.prev_cites == null ? "new" : p.prev_cites ? "yes" : "no"}</td>
                  <td className="small muted wrap-anywhere">{hosts.slice(0, 6).join(", ")}{hosts.length > 6 ? ` +${hosts.length - 6}` : ""}</td>
                  <td className="num">{fmtUsd(p.cost_usd, 3)}</td>
                </tr>
              );
            })}
            {d.prompts.length === 0 && <Empty cols={5}>No prompts asked yet. The weekly monitor asks them.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <div className="grid-2">
        <Section title="Domain mentions by platform" note="DataForSEO LLM Mentions, United States English index.">
          <div className="scroll"><table>
            <thead><tr><th>Platform</th><th className="num">Mentions</th><th className="num">AI search volume</th></tr></thead>
            <tbody>
              {d.domain.map((r) => <tr key={str(r.platform)}><td>{str(r.platform)}</td><td className="num">{fmtInt(r.mentions)}</td><td className="num">{fmtInt(r.ai_search_volume)}</td></tr>)}
              {d.domain.length === 0 && <Empty cols={3}>No mentions of the domain recorded.</Empty>}
            </tbody>
          </table></div>
        </Section>
        <Section title="AI search volume by keyword">
          <div className="scroll"><table>
            <thead><tr><th>Keyword</th><th className="num">AI volume</th><th>Measured</th></tr></thead>
            <tbody>
              {d.aiKeywords.map((r) => <tr key={str(r.keyword)}><td>{str(r.keyword)}</td><td className="num">{fmtInt(r.ai_search_volume)}</td><td className="muted">{fmtDay(r.collected_at)}</td></tr>)}
              {d.aiKeywords.length === 0 && <Empty cols={3}>Not measured yet.</Empty>}
            </tbody>
          </table></div>
        </Section>
      </div>

      <Section title="AI answers that mention the name" note={`Latest sample: ${fmtInt(domainRows.length)} answer(s) citing the domain, ${fmtInt(brandRows.length)} using the brand name, ${fmtInt(ours.length)} of those about this site.`}>
        <MentionTable rows={d.brand.filter((r) => r.about_project)} empty="No AI answer in the latest sample is about this site." />
        {d.brand.some((r) => !r.about_project) && (
          <details>
            <summary className="muted small">{fmtInt(d.brand.filter((r) => !r.about_project).length)} answer(s) about other brands with the same name</summary>
            <MentionTable rows={d.brand.filter((r) => !r.about_project)} empty="" />
          </details>
        )}
      </Section>
    </>
  );
}
