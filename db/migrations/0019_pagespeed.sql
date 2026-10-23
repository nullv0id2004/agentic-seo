-- 0019_pagespeed.sql
-- PageSpeed Insights: more per row (performance score, FCP, TBT, server response time, CrUX's overall category, the
-- audits that would save the most time) and how many pages each project measures. Field data CrUX reports for the
-- whole origin, because a page has too little traffic of its own, is stored once per strategy against the origin
-- (field_scope 'origin') and never as if it were the page's own. raw_vitals keeps its table-level grants and RLS.

alter table raw_vitals add column if not exists performance_score numeric;   -- Lighthouse performance score, 0 to 100 (lab)
alter table raw_vitals add column if not exists fcp_ms numeric;
alter table raw_vitals add column if not exists tbt_ms numeric;              -- total blocking time, the lab stand-in for INP
alter table raw_vitals add column if not exists ttfb_ms numeric;             -- lab: server response time; field: time to first byte
alter table raw_vitals add column if not exists field_scope text;            -- field rows: 'url' | 'origin'; null on lab and older rows
alter table raw_vitals add column if not exists field_category text;         -- field rows: CrUX overall_category (FAST, AVERAGE, SLOW)
alter table raw_vitals add column if not exists opportunities jsonb;         -- lab rows: [{id, title, display_value, savings_ms}]

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'raw_vitals_field_scope_check') then
    alter table raw_vitals add constraint raw_vitals_field_scope_check check (field_scope is null or field_scope in ('url', 'origin'));
  end if;
end $$;

-- Pages measured per run: the homepage, then the pages with the most Search Console clicks. Each page costs two
-- PageSpeed calls (mobile and desktop) against a quota shared by every project.
alter table projects add column if not exists vitals_page_limit int not null default 10;
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'projects_vitals_page_limit_range') then
    alter table projects add constraint projects_vitals_page_limit_range check (vitals_page_limit between 1 and 25);
  end if;
end $$;
