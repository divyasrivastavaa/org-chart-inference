-- db/migrations/007_company_run_index.sql  -  faster company pages
-- "Before vs Now" reads a company's last two refreshes. Without this index, a company that
-- was refreshed long ago has to scan past the lines of every newer run.
-- CONCURRENTLY: the app keeps working while it is built (so no begin / commit here).
-- Safe to run again.  Apply:  python scripts/run_sql.py db/migrations/007_company_run_index.sql

create index concurrently if not exists reporting_lines_company_run_idx
    on reporting_lines (company_id, run_id);
