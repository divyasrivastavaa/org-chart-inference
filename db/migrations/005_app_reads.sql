-- db/migrations/005_app_reads.sql  -  Phase 2: the app reads only from the database
--   1. sources: where each real company / organisation comes from (was data/real/sources.csv)
--   2. app_readonly: a role that can only READ this project's tables. The app's SQL tab
--      switches to it (SET LOCAL ROLE) inside a read-only transaction, so a visitor's query
--      can't write anything and can't see Supabase's own schemas (auth, storage, vault).
--      It has no login and no password.
--   3. Row level security policies for app_readonly. People with source 'pdl' are real
--      people: their rows are hidden unless the app sets app.show_pdl_names = 'on' (only
--      after the visitor entered DB_VIEW_PIN).
-- Safe to run again.  Apply:  python scripts/run_sql.py db/migrations/005_app_reads.sql

begin;

create table if not exists sources (
    source_id     bigint generated always as identity primary key,
    dataset       text not null,                 -- 'ASX/NZX leadership', 'UK government organograms'
    company_id    text not null references companies (company_id),
    company_name  text,
    url           text not null,
    what_it_shows text not null,                 -- 'Confirms ...' = an answer-key source
    date_checked  date,
    licence       text,
    unique (company_id, url, what_it_shows)
);
create index if not exists sources_company_idx on sources (company_id);
alter table sources enable row level security;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'app_readonly') then
        create role app_readonly nologin;
    end if;
end
$$;
grant app_readonly to current_user;              -- so the app can SET LOCAL ROLE to it
grant usage on schema public to app_readonly;
grant select on companies, people, employments, reporting_lines, runs, answer_key, sources
    to app_readonly;

drop policy if exists app_readonly_read on companies;
create policy app_readonly_read on companies for select to app_readonly using (true);
drop policy if exists app_readonly_read on employments;
create policy app_readonly_read on employments for select to app_readonly using (true);
drop policy if exists app_readonly_read on reporting_lines;
create policy app_readonly_read on reporting_lines for select to app_readonly using (true);
drop policy if exists app_readonly_read on runs;
create policy app_readonly_read on runs for select to app_readonly using (true);
drop policy if exists app_readonly_read on answer_key;
create policy app_readonly_read on answer_key for select to app_readonly using (true);
drop policy if exists app_readonly_read on sources;
create policy app_readonly_read on sources for select to app_readonly using (true);
drop policy if exists app_readonly_read on people;
create policy app_readonly_read on people for select to app_readonly
    using (source <> 'pdl' or current_setting('app.show_pdl_names', true) = 'on');

commit;
