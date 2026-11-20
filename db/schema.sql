-- db/schema.sql  -  Phase 2: database (Postgres / Supabase)
--
-- Run once on an empty database (Supabase: SQL Editor -> paste -> Run).
-- Safe to read top to bottom: every table is explained in db/README.md.
--
-- Main ideas
--   * History is never overwritten. A job change or title change ADDS a row to
--     employments; the old row is closed (end_date, is_current = false).
--     A new inference run ADDS rows to reporting_lines; the old ones get is_current = false.
--   * companies.needs_refresh marks the companies whose org chart must be re-inferred.
--     A trigger sets it whenever an employment row is added or changed.
--   * answer_key is only for measuring accuracy. The inference code never reads it.

begin;

-- ------------------------------------------------------------------ runs
-- One row per inference run (initial load, refresh, or a change made in the app).
create table runs (
    run_id               bigint generated always as identity primary key,
    started_at           timestamptz not null default now(),
    finished_at          timestamptz,
    trigger              text not null default 'refresh'
                         check (trigger in ('initial_load', 'refresh', 'app_change', 'manual',
                                            'pdl_import')),    -- a PDL API call (credits)
    note                 text,                       -- e.g. the cache file of a PDL call
    status               text not null default 'running'
                         check (status in ('running', 'succeeded', 'failed')),
    error                text,                       -- filled when status = 'failed'
    companies_processed  integer not null default 0,
    people_processed     integer not null default 0,
    lines_written        integer not null default 0, -- new reporting_lines rows (changed lines only)
    threshold            numeric(4, 3),              -- confidence needed to draw a line (0.7)
    gemini_calls         integer not null default 0,
    gemini_input_tokens  bigint  not null default 0,
    gemini_output_tokens bigint  not null default 0,
    pdl_credits_used     integer not null default 0,
    cost_usd             numeric(10, 4) not null default 0,
    code_version         text,                       -- git commit of the code that ran
    check (finished_at is null or finished_at >= started_at)
);
create index runs_started_at_idx on runs (started_at desc);
create unique index runs_pdl_import_note_idx on runs (note) where trigger = 'pdl_import';

-- ------------------------------------------------------------------ companies
create table companies (
    company_id        text primary key,              -- C001 (Benchmark), R01 (ASX/NZX), U01 (UK),
                                                     -- P-... (PDL), M001 (added by hand)
    name              text not null,
    industry          text,
    size_band         text,                          -- e.g. '51-200'
    country           text,
    website           text,
    source            text not null
                      check (source in ('benchmark', 'asx_nzx', 'uk_organogram', 'pdl',
                                        'pdl_history',   -- a past employer in PDL job history
                                        'manual')),      -- added by hand (db/changes.py)
    source_ref        text,                          -- id in the source (e.g. the PDL company id)
    needs_refresh     boolean not null default true, -- org chart must be re-inferred
    last_refreshed_at timestamptz,
    last_run_id       bigint references runs (run_id),
    created_at        timestamptz not null default now(),
    unique (source, source_ref)
);
-- Small index: only the few companies waiting for a refresh are in it
create index companies_needs_refresh_idx on companies (company_id) where needs_refresh;
create index companies_name_idx on companies (lower(name));

-- ------------------------------------------------------------------ people
-- A person exists once, even after moving between companies.
create table people (
    person_id    text primary key,                   -- existing data: the old employee_id
                                                     -- (C001-E0001); PDL: 'P-' + PDL person id
    full_name    text,                               -- may be withheld (UK organograms)
    source       text not null
                 check (source in ('benchmark', 'asx_nzx', 'uk_organogram', 'pdl', 'manual')),
    source_ref   text,                               -- id in the source (e.g. the PDL person id)
    linkedin_url text,
    created_at   timestamptz not null default now(),
    unique (source, source_ref)
);
create index people_name_idx on people (lower(full_name));

-- ------------------------------------------------------------------ employments
-- Every job a person has held: one row per (person, company, title) period.
-- Never updated in place except to CLOSE a row (end_date, is_current = false).
create table employments (
    employment_id          bigint generated always as identity primary key,
    person_id              text not null references people (person_id),
    company_id             text not null references companies (company_id),
    title                  text,                     -- as written in the source (messy is fine)
    department             text,
    location               text,
    seniority              text,                     -- from the source, e.g. PDL 'vp,director'
    start_date             date,                     -- PDL often gives only year-month: day 1
    end_date               date,                     -- null while the job is current
    is_current             boolean not null default true,
    change_type            text not null default 'initial'
                           check (change_type in (
                               'initial',            -- first load of existing data
                               'new',                -- person added by hand
                               'job_change',         -- moved here from another company
                               'title_change')),     -- new title, same company
                           -- Leaving is not a type: the old row is closed (end_date set).
    previous_employment_id bigint references employments (employment_id),  -- the row it replaced
    source                 text not null default 'manual',
    recorded_at            timestamptz not null default now(),  -- when WE learned about it
    check (end_date is null or start_date is null or end_date >= start_date),
    check (not (is_current and end_date is not null))
);
-- One current job per person per company (a person may hold jobs in two companies)
create unique index employments_one_current_idx
    on employments (person_id, company_id) where is_current;
create index employments_company_current_idx on employments (company_id) where is_current;
create index employments_person_idx on employments (person_id, start_date);

-- A new or changed CURRENT job (or a current job being closed) marks its company for an
-- org chart refresh. Past jobs (job history) don't change today's org chart.
create function mark_company_for_refresh() returns trigger
language plpgsql as $$
begin
    if new.is_current or (tg_op = 'UPDATE' and old.is_current) then
        update companies set needs_refresh = true
        where company_id = new.company_id and not needs_refresh;   -- only if not flagged yet
    end if;
    return new;
end;
$$;
create trigger employments_mark_refresh
    after insert or update on employments
    for each row execute function mark_company_for_refresh();

-- ------------------------------------------------------------------ reporting_lines
-- The inferred manager of each person in a company, one row per run in which it CHANGED.
-- manager_id null = the model abstained (reason says why).
create table reporting_lines (
    line_id             bigint generated always as identity primary key,
    run_id              bigint not null references runs (run_id),
    company_id          text not null references companies (company_id),
    person_id           text not null references people (person_id),
    employment_id       bigint references employments (employment_id),
    proposed_manager_id text references people (person_id),  -- best candidate, even if not drawn
    manager_id          text references people (person_id),  -- drawn line (confidence >= threshold)
    confidence          numeric(4, 3) check (confidence between 0 and 1),
    reason              text,                     -- why no line: 'confidence below threshold', ...
    judge               text not null default 'rules'
                        check (judge in ('rules', 'gemini', 'interim')),
    is_current          boolean not null default true,
    created_at          timestamptz not null default now(),
    check (manager_id is null or manager_id = proposed_manager_id),
    check (manager_id is distinct from person_id)
);
create unique index reporting_lines_one_current_idx
    on reporting_lines (company_id, person_id) where is_current;
create index reporting_lines_manager_current_idx
    on reporting_lines (manager_id) where is_current;     -- "who reports to X?"
create index reporting_lines_run_idx on reporting_lines (run_id);

-- ------------------------------------------------------------------ answer_key
-- True managers, only for measuring precision / coverage. Never read by the inference code.
create table answer_key (
    company_id      text not null references companies (company_id),
    person_id       text not null references people (person_id),
    true_manager_id text,     -- null = top of company. No foreign key on purpose: in the
                              -- Benchmark some true managers are missing from the data
                              -- (scenario 'manager_missing'), so they are not in people.
    true_level      smallint,
    evidence_type   text,     -- Benchmark: 'generator'; ASX/NZX: written source; UK: published
    source_url      text,
    scenario        text,     -- Benchmark hard cases: 'shared_manager', 'manager_missing', ...
    primary key (company_id, person_id)
);
create index answer_key_true_manager_idx on answer_key (true_manager_id);

-- ------------------------------------------------------------------ security
-- Supabase also exposes the public schema through its web API. Row level security with
-- no policies blocks that API completely; the app connects directly with its own role.
alter table runs            enable row level security;
alter table companies       enable row level security;
alter table people          enable row level security;
alter table employments     enable row level security;
alter table reporting_lines enable row level security;
alter table answer_key      enable row level security;

commit;
