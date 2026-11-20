"""
scripts/test_cleanup.py  -  Phase 2: tests refresh.py's cleanup rule (keep only the
reporting lines of each company's last 2 refreshes) on one company, then puts the
database back exactly as it was.

On C003, whose chart comes from one earlier refresh (run R0):
  1. change one person's title       -> refresh (run A): lines of R0 + A kept, 0 deleted
  2. change the title back           -> refresh (run B): R0 is now 3 refreshes old, so its
                                        lines are deleted; lines of A + B kept
  3. undo: delete runs A and B with their lines and the 2 test jobs, reopen the original
     job, and put R0's lines back exactly (same line_id, created_at), restore the company.
Before step 1, R0's lines and the company state are saved to data/cleanup_test.json, so
`python scripts/test_cleanup.py --undo` can finish the undo if the test stops halfway.
Run from the repo root:  python scripts/test_cleanup.py
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import refresh  # noqa: E402
from db import changes  # noqa: E402
from db.connect import get_connection  # noqa: E402

COMPANY, PERSON = "C003", "C003-E0036"
SAVE = ROOT / "data" / "cleanup_test.json"
LINE_COLS = ["line_id", "run_id", "company_id", "person_id", "employment_id",
             "proposed_manager_id", "manager_id", "confidence", "reason", "judge",
             "is_current", "created_at"]


def lines_per_run(c):
    return c.execute("select run_id, is_current, count(*) from reporting_lines "
                     "where company_id = %s group by 1, 2 order by 1", (COMPANY,)).fetchall()


def snapshot():
    with get_connection() as c:
        c.read_only = True
        waiting = c.execute("select count(*) from companies co where needs_refresh and exists"
                            " (select 1 from employments e where e.company_id = co.company_id"
                            " and e.is_current)").fetchone()[0]
        if waiting:
            sys.exit(f"{waiting} companies already need a refresh; stopping.")
        lines = c.execute(f"select {', '.join(LINE_COLS)} from reporting_lines "
                          "where company_id = %s", (COMPANY,)).fetchall()
        company = c.execute("select needs_refresh, last_refreshed_at, last_run_id from "
                            "companies where company_id = %s", (COMPANY,)).fetchone()
        job = c.execute("select employment_id, title from employments where person_id = %s "
                        "and is_current", (PERSON,)).fetchone()
        print(f"Before: {COMPANY} lines per run (run, current, count): {lines_per_run(c)}")
    return {"lines": [[v.isoformat() if isinstance(v, datetime) else
                       float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int)
                       else v for v in row] for row in lines],
            "company": [company[0], company[1].isoformat() if company[1] else None,
                        company[2]],
            "job": list(job), "runs": [], "opened": []}


def undo(s):
    with get_connection() as c:                   # one transaction: all or nothing
        ex = lambda q, *p: c.execute(q, p or None).rowcount          # noqa: E731
        n = {}
        n["lines deleted (test runs)"] = ex(
            "delete from reporting_lines where company_id = %s and run_id = any(%s)",
            COMPANY, s["runs"])
        n["test jobs deleted"] = ex(
            "delete from employments where employment_id = any(%s) and person_id = %s",
            s["opened"], PERSON)
        n["original job reopened"] = ex(
            "update employments set is_current = true, end_date = null "
            "where employment_id = %s", s["job"][0])
        keep = {r[0] for r in c.execute("select line_id from reporting_lines "
                                        "where company_id = %s", (COMPANY,)).fetchall()}
        back = [r for r in s["lines"] if r[0] not in keep]       # lines the cleanup deleted
        with c.cursor() as cur:
            cur.executemany(f"insert into reporting_lines ({', '.join(LINE_COLS)}) "
                            "overriding system value values ("
                            + ", ".join(["%s"] * len(LINE_COLS)) + ")", back)
        n["deleted lines put back (same ids)"] = len(back)
        n["lines made current again"] = ex(
            "update reporting_lines set is_current = true where line_id = any(%s)",
            [r[0] for r in s["lines"] if r[10]])
        ex("update companies set needs_refresh = %s, last_refreshed_at = %s, last_run_id = %s"
           " where company_id = %s", *s["company"], COMPANY)
        n["test runs deleted"] = ex("delete from runs where run_id = any(%s)", s["runs"])
    for k, v in n.items():
        print(f"  {k:<36} {v}")
    SAVE.unlink(missing_ok=True)


def check(s):
    """The company's lines, job and state are exactly as in the snapshot."""
    with get_connection() as c:
        c.read_only = True
        now = c.execute(f"select {', '.join(LINE_COLS)} from reporting_lines "
                        "where company_id = %s order by line_id", (COMPANY,)).fetchall()
        same_lines = [(r[0], r[1], r[10]) for r in now] == sorted(
            (r[0], r[1], r[10]) for r in s["lines"])
        job = c.execute("select employment_id, title from employments where person_id = %s "
                        "and is_current", (PERSON,)).fetchone()
        jobs = c.execute("select count(*) from employments where person_id = %s",
                         (PERSON,)).fetchone()[0]
        co = c.execute("select needs_refresh, last_run_id from companies where "
                       "company_id = %s", (COMPANY,)).fetchone()
        print(f"\nAfter undo: lines per run {lines_per_run(c)}")
        print(f"  lines identical to before: {same_lines} | job: {job} ({jobs} row) | "
              f"company needs_refresh={co[0]}, last_run_id={co[1]}")


def main():
    if "--undo" in sys.argv:
        undo(json.loads(SAVE.read_text(encoding="utf-8")))
        return
    if SAVE.exists():
        sys.exit("A test is half done: run  python scripts/test_cleanup.py --undo")
    s = snapshot()
    SAVE.write_text(json.dumps(s), encoding="utf-8")
    old = s["job"][1]

    def step(title, label):
        done = changes.change_title(PERSON, title)
        s["opened"] += done["opened"]
        SAVE.write_text(json.dumps(s), encoding="utf-8")
        run = refresh.main("manual")
        s["runs"].append(run)
        SAVE.write_text(json.dumps(s), encoding="utf-8")
        with get_connection() as c:
            print(f"  {label}: {COMPANY} lines per run (run, current, count): "
                  f"{lines_per_run(c)}\n")

    print(f"\n1) {PERSON}: '{old}' -> '{re.sub('Manager', 'Director', old)}'")
    step(re.sub("Manager", "Director", old), "after run A")
    print(f"2) {PERSON}: back to '{old}'")
    step(old, "after run B")
    print("3) undo")
    undo(s)
    check(s)


if __name__ == "__main__":
    main()
