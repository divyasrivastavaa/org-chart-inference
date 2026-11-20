-- db/migrations/003_costs.sql  -  Phase 2: cost tracking
-- runs already has pdl_credits_used, gemini_input_tokens, gemini_output_tokens and
-- cost_usd (db/schema.sql). This adds:
--   1. trigger 'pdl_import': a PDL API call, recorded as its own run (credits used)
--   2. runs.note: where the run came from, e.g. the cache file of a PDL call
--      (also stops the same PDL call being recorded twice)
-- Safe to run again.  Apply:  python scripts/run_sql.py db/migrations/003_costs.sql

begin;

alter table runs drop constraint if exists runs_trigger_check;
alter table runs add constraint runs_trigger_check check (trigger in
    ('initial_load', 'refresh', 'app_change', 'manual', 'pdl_import'));

alter table runs add column if not exists note text;
create unique index if not exists runs_pdl_import_note_idx
    on runs (note) where trigger = 'pdl_import';

commit;
