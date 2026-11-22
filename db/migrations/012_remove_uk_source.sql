-- db/migrations/012_remove_uk_source.sql  -  the UK organisations are no longer in the database
-- Run after: python scripts/remove_old_data.py uk_organogram
-- The UK files and results stay offline (data/raw/uk, scripts/build_uk.py, docs/uk_results.md).
-- Safe to run again (each constraint is dropped and added back).
-- Apply once:  python scripts/run_sql.py db/migrations/012_remove_uk_source.sql

begin;

alter table companies drop constraint companies_source_check;
alter table companies add constraint companies_source_check check (source in
    ('pdl', 'pdl_history', 'pdl_company', 'manual'));

alter table people drop constraint people_source_check;
alter table people add constraint people_source_check check (source in
    ('pdl', 'generated', 'manual'));

commit;
