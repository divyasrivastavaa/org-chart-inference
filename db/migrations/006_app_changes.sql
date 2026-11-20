-- db/migrations/006_app_changes.sql  -  Phase 2: every change made in the app is saved
--   1. change_log: one row per change made with db/changes.py (add a company or person,
--      change a title, change job, remove). It lists exactly which employments rows the
--      change opened and closed, so "Undo last change" can reverse it. judge = who should
--      place the changed people at the next refresh ('rules' or 'gemini').
--   2. saved_queries: queries saved from the app's SQL tab.
-- Safe to run again.  Apply:  python scripts/run_sql.py db/migrations/006_app_changes.sql

begin;

create table if not exists change_log (
    change_id       bigint generated always as identity primary key,
    created_at      timestamptz not null default now(),
    kind            text not null check (kind in ('add_company', 'add_person', 'change_title',
                                                  'change_job', 'remove_person')),
    person_id       text,
    company_ids     text[] not null default '{}',    -- companies marked for refresh
    opened          bigint[] not null default '{}',  -- employments rows this change opened
    closed          bigint[] not null default '{}',  -- employments rows this change closed
    added_people    text[] not null default '{}',
    added_companies text[] not null default '{}',
    judge           text not null default 'rules' check (judge in ('rules', 'gemini')),
    summary         text,                            -- one line, shown in the app
    undone_at       timestamptz                      -- set by "Undo last change"
);
create index if not exists change_log_open_idx on change_log (change_id desc)
    where undone_at is null;
alter table change_log enable row level security;

create table if not exists saved_queries (
    query_id   bigint generated always as identity primary key,
    name       text not null unique,
    sql        text not null,
    created_at timestamptz not null default now()
);
alter table saved_queries enable row level security;

-- The SQL tab's read-only role may read them too
grant select on change_log, saved_queries to app_readonly;
drop policy if exists app_readonly_read on change_log;
create policy app_readonly_read on change_log for select to app_readonly using (true);
drop policy if exists app_readonly_read on saved_queries;
create policy app_readonly_read on saved_queries for select to app_readonly using (true);

commit;
