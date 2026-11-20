-- db/migrations/001_pdl_history.sql  -  Phase 2: PDL job history
-- Changes an existing database (made with the first db/schema.sql) to the current schema:
--   1. companies.source may also be 'pdl_history' (a past employer from someone's history)
--   2. employments.change_type 'joined_company' is renamed 'job_change' (no rows used it)
--   3. employments.seniority: seniority from the source (PDL job_title_levels), e.g. 'vp'
--   4. Only CURRENT jobs mark a company for refresh. Loading a past job doesn't change
--      anyone's org chart today, so it no longer sets needs_refresh.
-- Apply once:  python scripts/run_sql.py db/migrations/001_pdl_history.sql

begin;

alter table companies drop constraint companies_source_check;
alter table companies add constraint companies_source_check check (source in
    ('benchmark', 'asx_nzx', 'uk_organogram', 'pdl', 'pdl_history', 'app'));

alter table employments drop constraint employments_change_type_check;
alter table employments add constraint employments_change_type_check check (change_type in
    ('initial', 'new_person', 'job_change', 'title_change'));

alter table employments add column if not exists seniority text;

create or replace function mark_company_for_refresh() returns trigger
language plpgsql as $$
begin
    -- a current job was added or changed, or a current job was closed
    if new.is_current or (tg_op = 'UPDATE' and old.is_current) then
        update companies set needs_refresh = true where company_id = new.company_id;
    end if;
    return new;
end;
$$;

commit;
