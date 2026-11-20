-- db/migrations/002_manual_changes.sql  -  Phase 2: add and change people
-- Renames two values that no row used yet, to the names db/changes.py uses:
--   companies.source / people.source  'app'        -> 'manual'  (added by hand)
--   employments.change_type           'new_person' -> 'new'     (person added by hand)
--   employments.source default        'app'        -> 'manual'
-- Safe to run again (each constraint is dropped and added back).
-- Apply once:  python scripts/run_sql.py db/migrations/002_manual_changes.sql

begin;

alter table companies drop constraint companies_source_check;
alter table companies add constraint companies_source_check check (source in
    ('benchmark', 'asx_nzx', 'uk_organogram', 'pdl', 'pdl_history', 'manual'));

alter table people drop constraint people_source_check;
alter table people add constraint people_source_check check (source in
    ('benchmark', 'asx_nzx', 'uk_organogram', 'pdl', 'manual'));

alter table employments drop constraint employments_change_type_check;
alter table employments add constraint employments_change_type_check check (change_type in
    ('initial', 'new', 'job_change', 'title_change'));

alter table employments alter column source set default 'manual';

commit;
