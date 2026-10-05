// Shared SQL fragments. Every fragment filters on project_id = $1 (withProject passes the project id
// as $1 and RLS scopes it again). $2 is the range in days wherever a window is used.
//
// Raw tables keep every collection, so a date collected twice appears twice. Each fragment keeps one
// run per date: the latest collection of that date.

/** Search Console: one run per date (the page grain, which holds the full totals), window bounds anchored
 *  on the latest collected date, page rows and query rows from that run. */
export const GSC = `
gsc_runs as (
  select distinct on (date) date, run_id from raw_gsc_performance
  where project_id = $1 and query is null order by date, collected_at desc
),
gsc_w as (
  select max(date) as e, max(date) - ($2::int - 1) as s, max(date) - (2 * $2::int - 1) as ps, max(date) - $2::int as pe from gsc_runs
),
gsc_pages as (
  select g.date, g.page, coalesce(g.clicks, 0) as clicks, coalesce(g.impressions, 0) as impressions, g.position
  from raw_gsc_performance g join gsc_runs r on r.date = g.date and r.run_id = g.run_id
  where g.project_id = $1 and g.query is null
),
gsc_queries as (
  select g.date, g.query, g.page, coalesce(g.clicks, 0) as clicks, coalesce(g.impressions, 0) as impressions, g.position
  from raw_gsc_performance g join gsc_runs r on r.date = g.date and r.run_id = g.run_id
  where g.project_id = $1 and g.query is not null
)`;

/** Daily Search Console totals across the window; collected = false marks a day with no collection. */
export const GSC_DAILY = `with ${GSC}
select d::date::text as date, exists (select 1 from gsc_runs r where r.date = d::date) as collected,
  coalesce(sum(p.clicks), 0)::int as clicks, coalesce(sum(p.impressions), 0)::int as impressions,
  sum(p.position * p.impressions) / nullif(sum(p.impressions), 0) as position
from gsc_w, generate_series(gsc_w.s, gsc_w.e, interval '1 day') d
left join gsc_pages p on p.date = d::date
where gsc_w.e is not null
group by d order by d`;

/** Current vs previous window totals. prev_days = 0 means no earlier data to compare with. */
export const GSC_TOTALS = `with ${GSC}
select
  (select e from gsc_w)::text as end_date,
  (select count(*) from gsc_runs, gsc_w where date between s and e)::int as cur_days,
  (select count(*) from gsc_runs, gsc_w where date between ps and pe)::int as prev_days,
  (select coalesce(sum(clicks), 0) from gsc_pages, gsc_w where date between s and e)::int as clicks,
  (select coalesce(sum(impressions), 0) from gsc_pages, gsc_w where date between s and e)::int as impressions,
  (select sum(position * impressions) / nullif(sum(impressions), 0) from gsc_pages, gsc_w where date between s and e) as position,
  (select coalesce(sum(clicks), 0) from gsc_pages, gsc_w where date between ps and pe)::int as prev_clicks,
  (select coalesce(sum(impressions), 0) from gsc_pages, gsc_w where date between ps and pe)::int as prev_impressions,
  (select sum(position * impressions) / nullif(sum(impressions), 0) from gsc_pages, gsc_w where date between ps and pe) as prev_position`;

/** GA4: one run per date, window anchored on the latest collected date. */
export const GA4 = `
ga_runs as (
  select distinct on (date) date, run_id from raw_ga4_daily where project_id = $1 order by date, collected_at desc
),
ga_w as (
  select max(date) as e, max(date) - ($2::int - 1) as s, max(date) - (2 * $2::int - 1) as ps, max(date) - $2::int as pe from ga_runs
),
ga as (
  select g.date, g.page_path, coalesce(g.channel, '(not set)') as channel, coalesce(g.sessions, 0) as sessions,
    coalesce(g.engaged_sessions, 0) as engaged, coalesce(g.conversions, 0) as conversions
  from raw_ga4_daily g join ga_runs r on r.date = g.date and r.run_id = g.run_id where g.project_id = $1
)`;

export const GA4_DAILY = `with ${GA4}
select d::date::text as date, exists (select 1 from ga_runs r where r.date = d::date) as collected,
  coalesce(sum(g.sessions), 0)::int as sessions, coalesce(sum(g.engaged), 0)::int as engaged, coalesce(sum(g.conversions), 0) as conversions
from ga_w, generate_series(ga_w.s, ga_w.e, interval '1 day') d
left join ga g on g.date = d::date
where ga_w.e is not null
group by d order by d`;

export const GA4_TOTALS = `with ${GA4}
select (select e from ga_w)::text as end_date,
  (select count(*) from ga_runs, ga_w where date between s and e)::int as cur_days,
  (select count(*) from ga_runs, ga_w where date between ps and pe)::int as prev_days,
  (select coalesce(sum(sessions), 0) from ga, ga_w where date between s and e)::int as sessions,
  (select coalesce(sum(engaged), 0) from ga, ga_w where date between s and e)::int as engaged,
  (select coalesce(sum(conversions), 0) from ga, ga_w where date between s and e) as conversions,
  (select coalesce(sum(sessions), 0) from ga, ga_w where date between ps and pe)::int as prev_sessions,
  (select coalesce(sum(engaged), 0) from ga, ga_w where date between ps and pe)::int as prev_engaged,
  (select coalesce(sum(conversions), 0) from ga, ga_w where date between ps and pe) as prev_conversions`;

/** Latest site audit run (a run in which site_crawl wrote pages), and its pages, one row per URL. */
export const LATEST_CRAWL = `
crawl_run as (
  select c.run_id from raw_crawl_pages c join runs r on r.id = c.run_id and r.project_id = c.project_id
  where c.project_id = $1 and r.workflow in ('post_deploy_audit', 'monthly_full') and c.status_code is not null
  order by c.collected_at desc limit 1
),
crawl as (
  select distinct on (c.url) c.* from raw_crawl_pages c, crawl_run
  where c.project_id = $1 and c.run_id = crawl_run.run_id and c.status_code is not null
  order by c.url, (c.word_count is not null) desc, c.collected_at desc
)`;

/** Best organic rank of a project domain (or a subdomain of one) in a raw_serp results array.
 *  domainsParam is the positional parameter holding the project's domains, lower case, without www. */
export function ourRank(resultsExpr: string, domainsParam: string): string {
  return `(select min((r->>'rank')::int) from jsonb_array_elements(coalesce(${resultsExpr}, '[]'::jsonb)) r
    where regexp_replace(lower(r->>'domain'), '^www\\.', '') = any(${domainsParam}::text[])
       or exists (select 1 from unnest(${domainsParam}::text[]) dd where lower(r->>'domain') like '%.' || dd))`;
}

/** True when any citation in a jsonb array points at a project domain. */
export function citesUs(citationsExpr: string, domainsParam: string): string {
  return `exists (select 1 from jsonb_array_elements(coalesce(${citationsExpr}, '[]'::jsonb)) c
    where regexp_replace(lower(coalesce(c->>'domain', split_part(split_part(c->>'url', '//', 2), '/', 1))), '^www\\.', '') = any(${domainsParam}::text[])
       or exists (select 1 from unnest(${domainsParam}::text[]) dd
                  where lower(coalesce(c->>'domain', split_part(split_part(c->>'url', '//', 2), '/', 1))) like '%.' || dd))`;
}

export function projectDomains(project: Record<string, unknown>): string[] {
  return ((project.domains as string[]) ?? []).map((d) => d.toLowerCase().replace(/^www\./, ""));
}
