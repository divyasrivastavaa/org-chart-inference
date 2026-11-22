"""
scripts/demo_changes.py  -  Phase 2: demo of db/changes.py on one company.

On N001: adds 2 new people, promotes one manager ("Engineering Manager, Platform" ->
"Engineering Director, Platform") and moves the Chief Data Officer to N002. Then runs
refresh.py: only N001 and N002 should be processed. Prints the companies refreshed, the
runs row and the moved person's full job history.

Everything the demo does is written to data/demo_changes.json, so
scripts/undo_demo.py can put the database back exactly as it was.
Run from the repo root:  python scripts/demo_changes.py
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import refresh  # noqa: E402
from db import changes  # noqa: E402
from db.connect import get_connection  # noqa: E402

RECORD = ROOT / "data" / "demo_changes.json"
COMPANY, OTHER = "N001", "N002"


def table(conn, sql, *params):
    cur = conn.execute(sql, params or None)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def save(record):
    RECORD.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")


def main():
    if RECORD.exists():
        sys.exit("The demo was already run. Run scripts/undo_demo.py first.")
    with get_connection() as conn:
        waiting = conn.execute(
            "select count(*) from companies c where needs_refresh and exists (select 1 "
            "from employments e where e.company_id = c.company_id and e.is_current)"
        ).fetchone()[0]
        if waiting:
            sys.exit(f"{waiting} companies already need a refresh; run refresh.py first, "
                     "so the demo shows only its own changes.")
        # Snapshot for undo: the lines and refresh state of the two companies right now
        before = {
            "lines": [r[0] for r in conn.execute(
                "select line_id from reporting_lines where company_id = any(%s) "
                "and is_current", ([COMPANY, OTHER],)).fetchall()],
            "companies": [list(r) for r in conn.execute(
                "select company_id, needs_refresh, last_refreshed_at, last_run_id "
                "from companies where company_id = any(%s)", ([COMPANY, OTHER],)).fetchall()],
        }
        manager = conn.execute(
            "select person_id, title from employments where company_id = %s and is_current "
            "and title = 'Engineering Manager, Platform'", (COMPANY,)).fetchone()
        senior = conn.execute(
            "select person_id, title from employments where company_id = %s and is_current "
            "and title = 'Chief Data Officer'", (COMPANY,)).fetchone()
    record = {"before": before, "steps": [], "run_ids": []}
    save(record)

    steps = [
        ("add person", lambda: changes.add_person("Demo Hire 1", COMPANY, "Software Engineer",
                                                  "Engineering")),
        ("add person", lambda: changes.add_person("Demo Hire 2", COMPANY, "Data Analyst",
                                                  "Data")),
        ("change title", lambda: changes.change_title(
            manager[0], re.sub("Manager", "Director", manager[1]))),
        ("change job", lambda: changes.change_job(senior[0], OTHER)),
    ]
    for what, do in steps:
        done = do()
        record["steps"].append({"what": what, **done})
        save(record)                               # saved after every step, for undo
        print(f"{what:<13} {done['person_id']}: companies marked {done['companies']}")

    print()
    run_id = refresh.main("manual")["run_id"]
    record["run_ids"].append(run_id)
    save(record)

    with get_connection() as conn:
        conn.read_only = True
        print("\nCompanies refreshed by this run:",
              [r[0] for r in conn.execute("select company_id from companies where "
                                          "last_run_id = %s order by 1", (run_id,))])
        print("\nRuns row:")
        print(table(conn, "select run_id, trigger, status, started_at, finished_at, "
                          "companies_processed, people_processed, lines_written, cost_usd "
                          "from runs where run_id = %s", run_id).T.to_string(header=False))
        print(f"\nJob history of {senior[0]} (oldest first):")
        print(table(conn, """
            select e.employment_id, c.company_id, e.title, e.start_date, e.end_date,
                   e.is_current, e.change_type, e.previous_employment_id
            from employments e join companies c using (company_id)
            where e.person_id = %s order by e.employment_id""", senior[0]).to_string(index=False))
        print(f"\nTheir reporting line now ({OTHER}):")
        print(table(conn, """
            select rl.person_id, rl.proposed_manager_id, rl.manager_id, rl.confidence, rl.reason
            from reporting_lines rl where rl.person_id = %s and rl.is_current""",
                    senior[0]).to_string(index=False))
    print(f"\nUndo with: python scripts/undo_demo.py   (record: {RECORD.relative_to(ROOT)})")


if __name__ == "__main__":
    main()
