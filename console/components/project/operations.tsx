import { ColumnChart } from "@/components/charts";
import { Empty, Meter, Section, StatTile, Status } from "@/components/ui";
import { withProject } from "@/lib/db";
import { fmtDate, fmtInt, fmtUsd, str, truncate } from "@/lib/format";
import type { TabProps } from "./overview";

type AgentLine = { agent: string; node: string | null; status: string | null; cost: number | null; rows: number | null; gaps: number | null; model: string | null };

export async function OperationsTab({ projectId, range, project }: TabProps) {
  const d = await withProject(projectId, async (q) => ({
    month: (await q(`select coalesce(sum(cost_usd), 0) as spent from runs where project_id = $1 and started_at >= date_trunc('month', now())`))[0] ?? {},
    runStats: (await q(`select count(*)::int as runs, count(*) filter (where status = 'done')::int as done,
        count(*) filter (where status = 'failed' or status like 'halted%')::int as failed, coalesce(avg(cost_usd) filter (where status = 'done'), 0) as avg_cost,
        coalesce(sum(cost_usd), 0) as cost
      from runs where project_id = $1 and started_at >= now() - make_interval(days => $2::int)`, [range]))[0] ?? {},
    daily: await q(`select d::date::text as day,
        coalesce(sum(a.cost_usd) filter (where a.node = 'collector'), 0)::float as apis,
        coalesce(sum(a.cost_usd) filter (where a.node is distinct from 'collector'), 0)::float as models
      from generate_series(current_date - ($2::int - 1), current_date, interval '1 day') d
      left join agent_logs a on a.project_id = $1 and a.created_at >= d and a.created_at < d + interval '1 day'
      group by d order by d`, [range]),
    byAgent: await q(`select agent, coalesce(node, '') as node, count(distinct run_id)::int as runs, coalesce(sum(cost_usd), 0) as cost,
        coalesce(sum(tokens_in), 0)::int as tokens_in, coalesce(sum(tokens_out), 0)::int as tokens_out, max(model) as model,
        count(*) filter (where status not in ('ok'))::int as not_ok
      from agent_logs where project_id = $1 and created_at >= now() - make_interval(days => $2::int)
      group by agent, node order by cost desc, agent`, [range]),
    runs: await q(`select r.id, r.workflow, r.trigger, r.status, r.started_at, r.ended_at, r.cost_usd, r.halt_reason, r.logical_date,
        (select json_agg(json_build_object('agent', a.agent, 'node', a.node, 'status', a.status, 'cost', a.cost_usd, 'model', a.model,
                'rows', (a.output->>'rows_written')::int, 'gaps', (a.output->>'gaps')::int) order by a.created_at)
           from agent_logs a where a.project_id = r.project_id and a.run_id = r.id) as agents
      from runs r where r.project_id = $1 and r.started_at >= now() - make_interval(days => $2::int)
      order by r.started_at desc limit 150`, [range]),
    gaps: await q(`select collector, reason, affected_scope, created_at from collection_gaps
      where project_id = $1 and created_at >= now() - make_interval(days => $2::int) order by created_at desc limit 150`, [range]),
    gate: (await q(`select count(*) filter (where detail->>'stage' = '1')::int as s1,
        count(*) filter (where detail->>'stage' = '1' and (detail->>'blocked')::boolean)::int as s1_blocked,
        count(*) filter (where stage2_verdict is not null)::int as s2,
        count(*) filter (where stage2_verdict = 'blocked')::int as s2_blocked,
        coalesce(sum(claims_cut), 0)::int as cut, coalesce(sum(claims_verified), 0)::int as verified
      from gate_results where project_id = $1 and created_at >= now() - make_interval(days => $2::int)`, [range]))[0] ?? {},
    gateRows: await q(`select source_agent, stage2_verdict, claims_verified, claims_cut, stage1_violations, detail, created_at
      from gate_results where project_id = $1 and (jsonb_array_length(coalesce(stage1_violations, '[]'::jsonb)) > 0 or stage2_verdict in ('blocked', 'corrected'))
      order by created_at desc limit 20`),
    guard: (await q(`select (select count(*)::int from raw_crawl_pages r where r.project_id = $1 and r.indexable = true and exists (
          select 1 from critical_rules c where c.project_id = r.project_id and c.assertion = 'must_noindex' and c.active
            and regexp_replace(r.url, '^https?://[^/]+', '') ~ c.url_pattern)) as indexed_protected,
        (select count(*)::int from approvals where project_id = $1 and severity = 'critical' and created_at >= now() - make_interval(days => $2::int)) as escalations`, [range]))[0] ?? {},
    audit: await q("select actor, event, detail, created_at from audit_log where project_id = $1 order by created_at desc limit 60"),
  }));
  const cap = Number(project.monthly_cost_cap_usd ?? 0);
  const spent = Number(d.month.spent ?? 0);
  const apiTotal = d.daily.reduce((a, r) => a + Number(r.apis), 0);
  const modelTotal = d.daily.reduce((a, r) => a + Number(r.models), 0);

  return (
    <>
      <div className="kpis">
        <div className="tile">
          <span className="label">Spend this month</span>
          <span className="value">{fmtUsd(spent)}</span>
          <Meter value={spent} max={cap} />
          <span className="delta">{cap ? `${((spent / cap) * 100).toFixed(0)}% of the ${fmtUsd(cap, 0)} cap; runs stop at the cap` : "no cap set"}</span>
        </div>
        <StatTile label={`Runs, last ${range} days`} value={fmtInt(d.runStats.runs)} foot={`${fmtInt(d.runStats.done)} done · ${fmtInt(d.runStats.failed)} failed or halted`} />
        <StatTile label="Average cost per finished run" value={fmtUsd(d.runStats.avg_cost, 3)} foot={`${fmtUsd(d.runStats.cost)} over the range`} />
        <StatTile label="Indexed protected routes" value={fmtInt(d.guard.indexed_protected)} foot="must stay at zero" />
        <StatTile label="Stage 1 gate blocks" value={`${fmtInt(d.gate.s1_blocked)} of ${fmtInt(d.gate.s1)}`} foot={`stage 2: ${fmtInt(d.gate.cut)} claims cut, ${fmtInt(d.gate.verified)} verified`} />
        <StatTile label="Critical escalations" value={fmtInt(d.guard.escalations)} foot={`last ${range} days`} />
      </div>

      <ColumnChart title="Spend per day" note={`data APIs ${fmtUsd(apiTotal)} · AI models ${fmtUsd(modelTotal)}`} format="usd" x={d.daily.map((r) => str(r.day))}
        series={[{ label: "Data APIs (DataForSEO, Google)", slot: 1, values: d.daily.map((r) => Number(r.apis)) },
                 { label: "AI models", slot: 2, values: d.daily.map((r) => Number(r.models)) }]} />

      <Section title="Cost by agent" note={`Last ${range} days. Collectors call data APIs; analysts and the verifier call AI models.`}>
        <div className="scroll"><table>
          <thead><tr><th>Agent</th><th>Kind</th><th className="num">Runs</th><th className="num">Cost</th><th className="num">Tokens in</th><th className="num">Tokens out</th><th>Model</th><th className="num">Not ok</th></tr></thead>
          <tbody>
            {d.byAgent.map((a) => (
              <tr key={`${str(a.agent)}${str(a.node)}`}>
                <td>{str(a.agent)}</td><td className="muted">{str(a.node)}</td><td className="num">{fmtInt(a.runs)}</td>
                <td className="num">{fmtUsd(a.cost, 3)}</td><td className="num">{fmtInt(a.tokens_in)}</td><td className="num">{fmtInt(a.tokens_out)}</td>
                <td className="muted small">{str(a.model)}</td><td className="num">{Number(a.not_ok) > 0 ? fmtInt(a.not_ok) : <span className="muted">0</span>}</td>
              </tr>
            ))}
            {d.byAgent.length === 0 && <Empty cols={8}>No agent activity in this range.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Runs" note="Open a run to see what each agent did and cost.">
        <div className="scroll"><table>
          <thead><tr><th>Workflow</th><th>Status</th><th>Started</th><th>Ended</th><th className="num">Cost</th></tr></thead>
          <tbody>
            {d.runs.map((r) => {
              const agents = (r.agents as AgentLine[] | null) ?? [];
              return (
                <tr key={str(r.id)}>
                  <td>
                    <details>
                      <summary>{str(r.workflow).replaceAll("_", " ")} <span className="muted small">{str(r.trigger)}{r.logical_date ? ` · ${str(r.logical_date).slice(0, 10)}` : ""}</span></summary>
                      <div className="muted small">run {str(r.id)}</div>
                      {r.halt_reason ? <p className="small">{str(r.halt_reason)}</p> : null}
                      <table className="small">
                        <thead><tr><th>Agent</th><th>Status</th><th className="num">Rows</th><th className="num">Gaps</th><th className="num">Cost</th></tr></thead>
                        <tbody>
                          {agents.map((a, i) => (
                            <tr key={i}><td>{a.agent} <span className="muted">{a.node ?? ""}</span></td><td>{a.status ?? ""}</td>
                              <td className="num">{fmtInt(a.rows)}</td><td className="num">{fmtInt(a.gaps)}</td><td className="num">{fmtUsd(a.cost, 4)}</td></tr>
                          ))}
                          {agents.length === 0 && <Empty cols={5}>no agent log</Empty>}
                        </tbody>
                      </table>
                    </details>
                  </td>
                  <td><Status value={r.status} /></td>
                  <td className="nowrap">{fmtDate(r.started_at)}</td><td className="nowrap muted">{fmtDate(r.ended_at)}</td>
                  <td className="num">{fmtUsd(r.cost_usd, 3)}</td>
                </tr>
              );
            })}
            {d.runs.length === 0 && <Empty cols={5}>No runs in this range.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Collection gaps" note="Data a collector could not get. Reports mark the affected numbers as missing rather than guessing.">
        <div className="scroll"><table>
          <thead><tr><th>Collector</th><th>Reason</th><th>Scope</th><th>When</th></tr></thead>
          <tbody>
            {d.gaps.map((g, i) => (
              <tr key={i}><td>{str(g.collector)}</td><td className="small">{str(g.reason)}</td><td className="small wrap-anywhere">{str(g.affected_scope)}</td><td className="nowrap muted">{fmtDate(g.created_at)}</td></tr>
            ))}
            {d.gaps.length === 0 && <Empty cols={4}>No gaps in this range.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Gate findings" note="Artifacts the deterministic gate or the verifier changed or stopped.">
        <div className="scroll"><table>
          <thead><tr><th>Agent</th><th>Stage 2</th><th className="num">Claims cut</th><th>Stage 1 findings</th><th>When</th></tr></thead>
          <tbody>
            {d.gateRows.map((g, i) => {
              const v = (g.stage1_violations as { check?: string; severity?: string; detail?: string }[] | null) ?? [];
              return (
                <tr key={i}>
                  <td>{str(g.source_agent)}</td><td>{g.stage2_verdict ? <Status value={g.stage2_verdict} /> : <span className="muted">–</span>}</td>
                  <td className="num">{fmtInt(g.claims_cut)}</td>
                  <td className="small">{v.slice(0, 5).map((x, k) => <div key={k}>{str(x.severity)} · {str(x.check)}: {truncate(x.detail, 160)}</div>)}{v.length > 5 ? <div className="muted">+{v.length - 5} more</div> : null}</td>
                  <td className="nowrap muted">{fmtDate(g.created_at)}</td>
                </tr>
              );
            })}
            {d.gateRows.length === 0 && <Empty cols={5}>Nothing blocked or corrected.</Empty>}
          </tbody>
        </table></div>
      </Section>

      <Section title="Audit log" note="Latest 60 events: approvals decided, executions, inspections and other recorded actions.">
        <div className="scroll"><table>
          <thead><tr><th>When</th><th>Actor</th><th>Event</th><th>Detail</th></tr></thead>
          <tbody>
            {d.audit.map((a, i) => (
              <tr key={i}>
                <td className="nowrap muted">{fmtDate(a.created_at)}</td><td className="small">{str(a.actor)}</td><td>{str(a.event).replaceAll("_", " ")}</td>
                <td className="small muted wrap-anywhere">{truncate(JSON.stringify(a.detail ?? {}), 200)}</td>
              </tr>
            ))}
            {d.audit.length === 0 && <Empty cols={4}>No audit events.</Empty>}
          </tbody>
        </table></div>
      </Section>
    </>
  );
}
