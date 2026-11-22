"""
scripts/check_pdl_load.py  -  read-only checks of the PDL companies and their generated people.
Prints counts only and exits with an error if a check fails.
Run from the repo root:  python scripts/check_pdl_load.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db.connect import get_connection  # noqa: E402

CHECKS = {
    "a company has under 100 current people": """
        select count(*) from (select c.company_id from companies c
        left join employments e on e.company_id = c.company_id and e.is_current
        where c.source = 'pdl_company' group by 1 having count(e.*) < 100) x""",
    "a generated person has no current job": """
        select count(*) from people p where p.source = 'generated' and not exists
        (select 1 from employments e where e.person_id = p.person_id and e.is_current)""",
    "a generated person has 2+ current jobs": """
        select count(*) from (select person_id from employments
        where source = 'generated' and is_current group by 1 having count(*) > 1) x""",
    "a generated person has 2+ past jobs": """
        select count(*) from (select person_id from employments
        where source = 'generated' and not is_current group by 1 having count(*) > 1) x""",
    "a generated person has no answer-key row": """
        select count(*) from employments e left join answer_key a
        on a.company_id = e.company_id and a.person_id = e.person_id
        where e.source = 'generated' and e.is_current and a.person_id is null""",
    "a changed job has no link to its past job": """
        select count(*) from employments where source = 'generated' and is_current
        and change_type in ('job_change', 'title_change') and previous_employment_id is null""",
    "an unchanged job has a past job": """
        select count(*) from employments where source = 'generated' and is_current
        and change_type = 'initial' and previous_employment_id is not null""",
    "a past job ends after the next job starts": """
        select count(*) from employments n join employments o
        on o.employment_id = n.previous_employment_id
        where n.source = 'generated' and o.end_date >= n.start_date""",
    "a job change stays at the same company": """
        select count(*) from employments n join employments o
        on o.employment_id = n.previous_employment_id
        where n.source = 'generated' and n.change_type = 'job_change'
        and o.company_id = n.company_id""",
    "a title change moves company": """
        select count(*) from employments n join employments o
        on o.employment_id = n.previous_employment_id
        where n.source = 'generated' and n.change_type = 'title_change'
        and o.company_id <> n.company_id""",
    "a generated job uses source 'manual'": """
        select count(*) from employments e join people p using (person_id)
        where p.source = 'generated' and e.source = 'manual'
        and e.recorded_at < (select coalesce(min(created_at), now()) from change_log)
        and not exists (select 1 from change_log)""",
    "a generated id starts with P-": """
        select count(*) from people where source = 'generated' and person_id like 'P-%'""",
}

failed = 0
with get_connection() as conn:
    conn.read_only = True
    conn.execute("set local statement_timeout = '280s'")
    for label, sql in CHECKS.items():
        n = conn.execute(sql).fetchone()[0]
        failed += bool(n)
        print(f"{'ok  ' if not n else 'FAIL'} {label}: {n:,}")
    row = conn.execute("""select (select count(*) from companies where source = 'pdl_company'),
        (select count(*) from people where source = 'generated'),
        (select count(*) from employments e join people p using (person_id)
         where p.source = 'generated' and not e.is_current),
        pg_database_size(current_database())""").fetchone()
print(f"\ncompanies {row[0]:,}, people {row[1]:,}, past jobs {row[2]:,}, "
      f"database {row[3] / 1e6:.1f} MB ({row[3] / 1048576:.1f} MiB)")
sys.exit(1 if failed else 0)
