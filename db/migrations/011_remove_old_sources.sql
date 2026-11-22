-- db/migrations/011_remove_old_sources.sql  -  the Benchmark and ASX/NZX data is gone
-- Run after scripts/remove_old_data.py: the two old source values are no longer allowed.
-- Safe to run again (each constraint is dropped and added back).
-- Apply once:  python scripts/run_sql.py db/migrations/011_remove_old_sources.sql

begin;

alter table companies drop constraint companies_source_check;
alter table companies add constraint companies_source_check check (source in
    ('uk_organogram', 'pdl', 'pdl_history', 'pdl_company', 'manual'));

alter table people drop constraint people_source_check;
alter table people add constraint people_source_check check (source in
    ('uk_organogram', 'pdl', 'generated', 'manual'));

commit;
