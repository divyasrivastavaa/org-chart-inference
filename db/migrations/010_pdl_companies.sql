-- db/migrations/010_pdl_companies.sql  -  real companies (PDL free dataset) with generated people
-- Adds two source values:
--   companies.source 'pdl_company'  a real company from PDL's free company dataset
--   people.source    'generated'    a person made by generate_pdl_people.py
-- Not the same as source 'pdl' (real people from the PDL person API, shown with extra privacy
-- rules). Safe to run again (each constraint is dropped and added back).
-- Apply once:  python scripts/run_sql.py db/migrations/010_pdl_companies.sql

begin;

alter table companies drop constraint companies_source_check;
alter table companies add constraint companies_source_check check (source in
    ('benchmark', 'asx_nzx', 'uk_organogram', 'pdl', 'pdl_history', 'pdl_company', 'manual'));

alter table people drop constraint people_source_check;
alter table people add constraint people_source_check check (source in
    ('benchmark', 'asx_nzx', 'uk_organogram', 'pdl', 'generated', 'manual'));

commit;
