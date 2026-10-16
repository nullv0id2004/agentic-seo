import { ColumnChart } from "@/components/charts";
import { DataTable, type DRow } from "@/components/datatable";
import { Section, StatTile, Status } from "@/components/ui";
import { withProject, type Row } from "@/lib/db";
import { fmtDay, fmtInt, num, str, truncate } from "@/lib/format";
import type { TabProps } from "./overview";

type Phase = "foundation" | "growth" | "authority";

const PHASE: Record<Phase, { label: string; help: string }> = {
  foundation: { label: "Foundation", help: "A new or thin site with few brand signals. Build consistent brand profiles and quality listings before any outreach, so later links look natural." },
  growth: { label: "Growth", help: "Some content and some links. Fill the gaps your competitors' links show, then earn links inside articles and resource pages." },
  authority: { label: "Authority", help: "An established site. Scale with partnerships, podcasts and content others cite, and keep the profile clean." },
};

const SIGNAL: Record<string, string> = {
  domain_rank: "Domain rank", site_age_years: "Years since the first link", indexable_pages: "Indexable pages",
  profiles_listed: "Profiles and listings done", referring_domains: "Referring domains",
};

const KIND: Record<string, string> = { profile: "Brand profile", directory: "Directory", review: "Review site" };

const CATEGORY: Record<string, string> = {
  directory: "Directory", resource_page: "Resource page", forum: "Forum", blog: "Blog", news: "News", other: "Other",
};

const BUCKET: Record<string, string> = {
  branded: "Branded", naked_url: "Naked URL", generic: "Generic", partial_match: "Partial match", exact_match: "Exact match", other: "Other or empty",
};

const FLAG: Record<string, string> = { velocity_spike: "Link velocity", exact_match_anchors: "Exact-match anchors", spammy_links: "Spammy links" };

function domainOf(url: unknown): string {
  return str(url).replace(/^https?:\/\//, "").replace(/\/$/, "");
}

function yesNo(v: unknown) {
  return v === true ? <span className="pill ok">yes</span> : v === false ? <span className="muted">no</span> : <span className="muted">–</span>;
}

export async function LinksTab({ projectId, project }: TabProps) {
  const d = await withProject(projectId, async (q) => {
    const plan = (await q("select * from link_plans where project_id = $1 order by created_at desc limit 1"))[0];
    if (!plan) return { plan: undefined, opps: [] as Row[], own: undefined };
    return {
      plan,
      // checklist in the config's priority order; prospects as the analyst ranks them: quick-win types first, then reach
      opps: await q(`select o.kind, o.domain, o.title, o.category, o.status, o.in_same_as, o.links_to_you, o.competitors, o.competitor_domains, o.rank,
          o.spam_score, o.action, o.linking_url, o.linking_title, o.first_seen_at, o.done_at
        from link_opportunities o left join link_targets t on t.project_id = o.project_id and t.domain = o.domain
        where o.project_id = $1 and (o.last_run_id = $2 or o.status = 'won')
        order by o.status = 'won', coalesce(t.priority, 100),
          array_position(array['directory','resource_page','forum','blog','news','other']::text[], o.category) nulls last,
          o.competitors desc nulls last, o.rank desc nulls last, o.title`, [plan.run_id]),
      own: (await q("select rank, referring_domains, backlinks, first_seen from raw_backlink_summary where project_id = $1 and run_id = $2 and role = 'own' limit 1",
        [plan.run_id]))[0],
    };
  });

  if (!d.plan) {
    return (
      <div className="card">
        <strong>No link plan yet.</strong> The monthly run builds one when <code>linkbuilding</code> is in this project&apos;s enabled agents:
        the site&apos;s authority phase, the three tactics that fit it, the profiles and listings still to do, prospects from the competitor
        backlink gap, anchor text safety and link velocity.
      </div>
    );
  }

  const plan = d.plan;
  const phase = (plan.phase as Phase | null) ?? null;
  const signals = (plan.signals as Row[]) ?? [];
  const tactics = (plan.tactics as Row[]) ?? [];
  const anchors = (plan.anchors as Row[]) ?? [];
  const flags = (plan.flags as Row[]) ?? [];
  const comps = (plan.competitors as Row[]) ?? [];
  const velocity = plan.velocity as { months: { month: string; new_domains: number }[]; program_month: number; guideline: string; last_month_new: number } | null;
  const copy = plan.profile_copy as { name: string; short?: string | null; medium?: string | null; long?: string | null } | null;
  const checklist = d.opps.filter((o) => o.kind !== "prospect");
  const prospects = d.opps.filter((o) => o.kind === "prospect");
  const done = checklist.filter((o) => o.status === "done").length;
  const open = prospects.filter((o) => o.status === "todo").length;
  const won = prospects.filter((o) => o.status === "won").length;

  const checkRows: DRow[] = checklist.map((o) => ({
    key: `${o.kind}|${o.domain}`,
    facet: str(o.status),
    cells: [
      <a key="p" href={`https://${str(o.domain)}/`} target="_blank" rel="noreferrer">{str(o.title)}</a>,
      <span key="k">{KIND[str(o.kind)] ?? str(o.kind)}</span>,
      <Status key="s" value={o.status} />,
      yesNo(o.in_same_as), yesNo(o.links_to_you),
      <span key="a" className="small">{str(o.action)}</span>,
    ],
    values: [str(o.title), KIND[str(o.kind)] ?? str(o.kind), str(o.status), o.in_same_as ? "yes" : "no", o.links_to_you ? "yes" : "no", str(o.action)],
  }));

  const prospectRows: DRow[] = prospects.map((o) => {
    const comp = (o.competitor_domains as string[] | null) ?? [];
    return {
      key: str(o.domain),
      facet: o.status === "won" ? "won" : str(o.category ?? "unsorted"),
      cells: [
        <a key="d" href={`https://${str(o.domain)}/`} target="_blank" rel="noreferrer">{str(o.domain)}</a>,
        <span key="c">{CATEGORY[str(o.category)] ?? <span className="muted">–</span>}</span>,
        o.linking_url ? (
          <span key="p" className="small"><a href={str(o.linking_url)} target="_blank" rel="noreferrer nofollow">{truncate(o.linking_title || str(o.linking_url).replace(/^https?:\/\//, ""), 80)}</a></span>
        ) : <span key="p" className="muted">–</span>,
        <span key="n" title={comp.join(", ")}>{fmtInt(o.competitors)}</span>,
        fmtInt(o.rank), fmtInt(o.spam_score),
        <span key="a" className="small">{o.action ? str(o.action) : <span className="muted">–</span>}</span>,
        <Status key="s" value={o.status} />,
      ],
      values: [str(o.domain), CATEGORY[str(o.category)] ?? "", str(o.linking_title ?? o.linking_url ?? ""), num(o.competitors), num(o.rank), num(o.spam_score), str(o.action), str(o.status)],
    };
  });
  const skipped = Number(plan.prospects_skipped ?? 0);
  const categories = Array.from(new Set(prospects.filter((o) => o.status !== "won").map((o) => str(o.category ?? "unsorted"))));

  return (
    <>
      <div className="kpis cols-4">
        <StatTile label="Authority phase" value={phase ? PHASE[phase].label : "Not measured"} foot={`plan of ${fmtDay(plan.created_at)}`} />
        <StatTile label="Domain rank" value={d.own?.rank != null ? `${fmtInt(d.own.rank)} of 100` : "–"} foot={`${fmtInt(d.own?.referring_domains)} referring domain(s)`} />
        <StatTile label="Profiles and listings done" value={`${fmtInt(done)} of ${fmtInt(checklist.length)}`} foot="ticked off from the crawl and the links, not by hand" />
        <StatTile label="Gap prospects" value={fmtInt(open)} foot={won ? `${fmtInt(won)} now link to you` : "sites linking to 2+ competitors, not you"} />
      </div>

      {flags.map((f, i) => (
        <div key={i} className="card alert"><strong>{FLAG[str(f.kind)] ?? str(f.kind)}.</strong> {str(f.detail)}</div>
      ))}

      <Section title="Where the site stands" note={phase ? PHASE[phase].help : "Nothing about the site's links or pages was collected in this run."}>
        <div className="scroll"><table>
          <thead><tr><th>Signal</th><th className="num">Value</th><th>Points to</th><th>From</th></tr></thead>
          <tbody>
            {signals.map((s, i) => (
              <tr key={i}>
                <td>{SIGNAL[str(s.name)] ?? str(s.name)}</td>
                <td className="num">{s.name === "site_age_years" ? str(s.value) : fmtInt(s.value)}</td>
                <td>{s.points_to ? PHASE[s.points_to as Phase].label : <span className="muted">context only</span>}</td>
                <td className="small muted">{str(s.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
        <p className="small muted">The phase is the lower middle of the signals that vote, so one strong number does not skip the foundation work.</p>
      </Section>

      <Section title="Recommended next" note="The three tactics that fit the phase and what is still undone. Effort and typical results are playbook guidance, not measurements.">
        <div className="tactics">
          {tactics.map((t, i) => (
            <div key={i} className="card tactic">
              <span className="step">{i + 1}</span>
              <h3>{str(t.name)}</h3>
              <p className="small">{str(t.why)}</p>
              <p className="small"><strong>First step:</strong> {str(t.first_action)}</p>
              <p className="small muted">Effort: {str(t.effort)}. Typical: {str(t.typical)}.</p>
            </div>
          ))}
          {tactics.length === 0 ? <p className="muted">No tactics: nothing about the site was measured in this run.</p> : null}
        </div>
      </Section>

      <Section title="Brand profiles and listings" note="One name everywhere. A profile is done when the site's Organization schema lists it in sameAs; a directory or review site when it lists or links to the site. The list itself lives in the project's config.">
        <DataTable columns={[{ label: "Platform" }, { label: "Type" }, { label: "Status" }, { label: "In sameAs" }, { label: "Links to you" }, { label: "Next step", sort: false }]}
          rows={checkRows} facets={[{ key: "todo", label: "To do" }, { key: "done", label: "Done" }]} defaultFacet="todo"
          filename="link-checklist" searchLabel="Filter platforms" empty="No link targets configured for this project." />
      </Section>

      {copy && (copy.short || copy.medium || copy.long) ? (
        <Section title="Profile descriptions" note={`Drafted from the project's description and homepage, checked against the brand rules. Use the same text, and the name ${copy.name}, on every profile.`}>
          {(["short", "medium", "long"] as const).map((k) => copy[k] ? (
            <details key={k} open={k === "short"}>
              <summary>{k === "short" ? "Short (up to 50 words)" : k === "medium" ? "Medium (up to 150 words)" : "Long (up to 300 words)"}</summary>
              <p className="copy">{copy[k]}</p>
            </details>
          ) : null)}
        </Section>
      ) : null}

      <Section title="Prospects from the backlink gap" note={`Sites that link to two or more competitors but not to you, strongest first within each type, each with the page on it that links to a competitor. Spam (score above 10), weak domains (rank under 20), namesakes, the competitors themselves and sites that link to everything are left out${skipped ? `, and ${fmtInt(skipped)} more were judged not real opportunities (job-listing scrapers, spam or unrelated sites)` : ""}. Directories and listings are the quick wins; no outreach emails are drafted.`}>
        <DataTable columns={[{ label: "Site" }, { label: "Type" }, { label: "Linking page", sort: false, title: "a page on this site that links to a competitor" },
          { label: "Competitors", num: true, title: "how many competitors this site links to" },
          { label: "Domain rank", num: true }, { label: "Spam", num: true, title: "DataForSEO spam score, 0 to 100" },
          { label: "How to earn it", sort: false }, { label: "Status" }]}
          rows={prospectRows} facets={[...categories.map((c) => ({ key: c, label: CATEGORY[c] ?? "Not sorted" })), ...(won ? [{ key: "won", label: "Won" }] : [])]}
          filename="link-prospects" searchLabel="Filter sites" empty="No prospects: no site links to two or more competitors without linking to you." />
      </Section>

      <div className="grid-2">
        <Section title="Link velocity" note={velocity ? `Month ${velocity.program_month} of the link programme (from the first link found): aim for ${velocity.guideline}. 50 or more in one month looks unnatural.` : "No links collected for the site yet."}>
          {velocity ? (
            <ColumnChart title="New referring domains by month first seen" note="live referring domains only" x={velocity.months.map((m) => m.month)}
              series={[{ label: "New referring domains", slot: 1, values: velocity.months.map((m) => m.new_domains) }]} xLabel="Month" />
          ) : null}
        </Section>

        <Section title="Anchor text" note="How the links to the site are worded. Exact-match keyword anchors above 5% are a penalty risk; branded and plain anchors keep a profile natural.">
          <div className="scroll"><table>
            <thead><tr><th>Anchor type</th><th className="num">Links</th><th className="num">Share</th><th>Natural range</th></tr></thead>
            <tbody>
              {anchors.map((a, i) => (
                <tr key={i}>
                  <td>{BUCKET[str(a.bucket)] ?? str(a.bucket)}</td>
                  <td className="num">{fmtInt(a.backlinks)}</td>
                  <td className="num">{`${Number(a.share_pct).toFixed(1)}%`}</td>
                  <td className="small">{a.safe_range ? str(a.safe_range) : <span className="muted">–</span>}
                    {a.within === false ? <span className={`pill ${a.bucket === "exact_match" ? "critical" : "plain"}`} style={{ marginLeft: 8 }}>{a.bucket === "exact_match" ? "too high" : "outside"}</span> : null}</td>
                </tr>
              ))}
              {anchors.length === 0 ? <tr><td colSpan={4} className="muted">No anchors collected yet.</td></tr> : null}
            </tbody>
          </table></div>
          {anchors.length > 0 && anchors.every((a) => a.within == null) ? <p className="small muted">Fewer than 10 links: too few to judge the mix.</p> : null}
        </Section>
      </div>

      {comps.length > 0 ? (
        <Section title="Competitor authority" note="Realistic competitors are within 15 points of your domain rank; their links are the easiest to copy. Aspirational ones show where the field tops out.">
          <div className="scroll"><table>
            <thead><tr><th>Competitor</th><th className="num">Domain rank</th><th className="num">Referring domains</th><th>Tier</th></tr></thead>
            <tbody>
              {comps.map((c, i) => (
                <tr key={i}>
                  <td>{domainOf(c.url)}</td><td className="num">{fmtInt(c.domain_rank)}</td><td className="num">{fmtInt(c.referring_domains)}</td>
                  <td>{c.tier ? (c.tier === "realistic" ? "Realistic" : "Aspirational") : <span className="muted">–</span>}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </Section>
      ) : null}
    </>
  );
}
