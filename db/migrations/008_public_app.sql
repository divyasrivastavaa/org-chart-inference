-- db/migrations/008_public_app.sql  -  the app is open to everyone (no PINs)
-- The SQL tab's read-only role must not reveal the past jobs of PDL people (real people):
--   1. employments: a PDL person's closed jobs are hidden; their current job stays visible.
--      PDL people are the only ones whose id starts with 'P-' (M-P... = added in the app).
--   2. companies: the past employers imported with the PDL history are hidden.
-- Safe to run again.  Apply:  python scripts/run_sql.py db/migrations/008_public_app.sql

begin;

drop policy if exists app_readonly_read on employments;
create policy app_readonly_read on employments for select to app_readonly
    using (is_current or person_id not like 'P-%');

drop policy if exists app_readonly_read on companies;
create policy app_readonly_read on companies for select to app_readonly
    using (source <> 'pdl_history');

commit;
