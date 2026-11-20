-- db/migrations/004_trigger_once.sql  -  Phase 2: lighter refresh trigger
-- The trigger used to rewrite the company row for EVERY current job added or changed. The
-- full load added 158k jobs, so the 766-row companies table grew to 21 MB of old row
-- copies. Now it only updates a company that isn't flagged yet (once per company).
-- Safe to run again.  Apply:  python scripts/run_sql.py db/migrations/004_trigger_once.sql
-- One-time cleanup of the space already used (cannot run inside a transaction):
--   vacuum full companies;   (done once, 20 Nov 2026)

begin;

create or replace function mark_company_for_refresh() returns trigger
language plpgsql as $$
begin
    -- a current job was added or changed, or a current job was closed
    if new.is_current or (tg_op = 'UPDATE' and old.is_current) then
        update companies set needs_refresh = true
        where company_id = new.company_id and not needs_refresh;   -- only if not flagged yet
    end if;
    return new;
end;
$$;

commit;
