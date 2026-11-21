-- db/migrations/009_short_pdl_ids.sql  -  short ids for PDL companies
-- company_id 'P-<long PDL id>' becomes P01, P02 ... (Atlassian = P01, then past employers
-- A-Z). The PDL id stays in companies.source_ref (16 past employers never had one).
-- Every table that points to a company moves with it, in one transaction. Refresh flags and
-- dates are kept. Person ids (P-...) don't change.
-- Safe to run again: once no company id starts with 'P-', it does nothing.
-- Apply:  python scripts/run_sql.py db/migrations/009_short_pdl_ids.sql

begin;

lock table companies, employments, reporting_lines, answer_key, sources, change_log
    in share row exclusive mode;

create temp table pdl_ids on commit drop as
select old_id,
       'P' || case when n < 10 then '0' else '' end || n as new_id,   -- P01 ... P99, P100 ...
       source_ref, needs_refresh
from (select company_id as old_id, source_ref, needs_refresh,
             row_number() over (order by source = 'pdl' desc, lower(name), company_id)
             + (select coalesce(max(substring(company_id from '^P([0-9]+)$')::int), 0)
                from companies) as n
      from companies
      where source in ('pdl', 'pdl_history') and company_id like 'P-%') as numbered;

-- The new row first (without source_ref, which must stay unique), then move the references
insert into companies (company_id, name, industry, size_band, country, website, source,
                       source_ref, needs_refresh, last_refreshed_at, last_run_id, created_at)
select m.new_id, c.name, c.industry, c.size_band, c.country, c.website, c.source,
       null, c.needs_refresh, c.last_refreshed_at, c.last_run_id, c.created_at
from companies c join pdl_ids m on m.old_id = c.company_id;

update employments x set company_id = m.new_id from pdl_ids m where x.company_id = m.old_id;
update reporting_lines x set company_id = m.new_id from pdl_ids m where x.company_id = m.old_id;
update answer_key x set company_id = m.new_id from pdl_ids m where x.company_id = m.old_id;
update sources x set company_id = m.new_id from pdl_ids m where x.company_id = m.old_id;
update change_log l
set company_ids = (select coalesce(array_agg(coalesce(m.new_id, c) order by o), '{}')
                   from unnest(l.company_ids) with ordinality u(c, o)
                   left join pdl_ids m on m.old_id = u.c),
    added_companies = (select coalesce(array_agg(coalesce(m.new_id, c) order by o), '{}')
                       from unnest(l.added_companies) with ordinality u(c, o)
                       left join pdl_ids m on m.old_id = u.c)
where exists (select 1 from unnest(l.company_ids || l.added_companies) c
              where c like 'P-%');

delete from companies c using pdl_ids m where c.company_id = m.old_id;

-- Put back the PDL id, and the refresh flag (the employments trigger sets it when a job moves)
update companies c set source_ref = m.source_ref, needs_refresh = m.needs_refresh
from pdl_ids m where c.company_id = m.new_id;

commit;
