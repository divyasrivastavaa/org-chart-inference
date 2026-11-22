"""
scripts/remove_old_data.py  -  removes every company of one or more sources, with their people,
jobs, reporting lines, answer key and sources. Used for: benchmark, asx_nzx, uk_organogram.
Kept: everything else, including run history.

How it stays fast and safe:
  1. Checks first (hash joins): no job links old and kept data; per-company counts are recorded.
  2. Seven foreign keys have no full index, so each deleted row would scan a whole table. Only
     those are dropped for the deletion. Their definitions are saved first to
     data/backup/fk_during_removal.json, so an interrupted run can restore them when rerun.
  3. One indexed DELETE statement per company, committed separately. If a company's counts
     differ from step 1, that company is rolled back and the run stops.
  4. The foreign keys are added back and validated against every remaining row.
Safe to rerun: it continues with the companies still there. Take a backup first
(scripts/backup_db.py). Prints counts and times only.
Run from the repo root:  python scripts/remove_old_data.py uk_organogram
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db.connect import get_connection  # noqa: E402

ALLOWED = {"benchmark", "asx_nzx", "uk_organogram"}
OLD = sys.argv[1:]
RECOVERY = ROOT / "data" / "backup" / "fk_during_removal.json"
SLOW_KEYS = {("employments", "company_id"), ("employments", "previous_employment_id"),
             ("reporting_lines", "employment_id"), ("reporting_lines", "person_id"),
             ("reporting_lines", "proposed_manager_id"), ("reporting_lines", "manager_id"),
             ("answer_key", "person_id")}
TABLES = {"companies": None, "people": "people", "employments": "jobs",
          "reporting_lines": "lines", "answer_key": "answers", "sources": "sources"}

PLAN = """
    select c.company_id,
           coalesce(s.n, 0) as sources, coalesce(r.n, 0) as lines, coalesce(a.n, 0) as answers,
           coalesce(e.n, 0) as jobs, coalesce(e.current_jobs, 0) as current_jobs,
           coalesce(e.people, 0) as people
    from companies c
    left join (select company_id, count(*) n from sources group by 1) s using (company_id)
    left join (select company_id, count(*) n from reporting_lines group by 1) r using (company_id)
    left join (select company_id, count(*) n from answer_key group by 1) a using (company_id)
    left join (select company_id, count(*) n, count(*) filter (where is_current) current_jobs,
                      count(distinct person_id) people
               from employments group by 1) e using (company_id)
    where c.source = any(%s)
    order by c.company_id"""

DELETE_ONE = """
    with s as (delete from sources where company_id = %(c)s returning 1),
         r as (delete from reporting_lines where company_id = %(c)s returning 1),
         a as (delete from answer_key where company_id = %(c)s returning 1),
         e as (delete from employments where company_id = %(c)s and is_current
               returning person_id),
         p as (delete from people where person_id in (select person_id from e)
               and source = any(%(old)s) returning 1),
         co as (delete from companies where company_id = %(c)s and source = any(%(old)s)
                returning 1)
    select (select count(*) from s), (select count(*) from r), (select count(*) from a),
           (select count(*) from e), (select count(*) from p), (select count(*) from co)"""


def clock(label, started):
    print(f"[time] {label}: {time.time() - started:.1f} s", flush=True)


def slow_keys(conn):
    rows = conn.execute("""select conrelid::regclass::text, conname, pg_get_constraintdef(oid)
        from pg_constraint where contype = 'f'
        and conrelid in ('employments'::regclass, 'reporting_lines'::regclass,
                         'answer_key'::regclass)""").fetchall()
    found = []
    for table, name, definition in rows:
        column = definition.split("(", 1)[1].split(")", 1)[0]
        if (table, column) in SLOW_KEYS:
            found.append({"table": table, "name": name, "definition": definition})
    return found


def restore_keys(conn, keys):
    """Adds back any missing key, then validates each one against the remaining rows."""
    have = {r[0] for r in conn.execute(
        "select conname from pg_constraint where contype = 'f' "
        "and connamespace = 'public'::regnamespace").fetchall()}
    for k in keys:
        if k["name"] not in have:
            conn.execute(f'alter table {k["table"]} add constraint "{k["name"]}" '
                         f'{k["definition"]} not valid')          # names from the database
    for k in keys:
        conn.execute(f'alter table {k["table"]} validate constraint "{k["name"]}"')
    RECOVERY.unlink(missing_ok=True)


def main():
    if not OLD or not set(OLD) <= ALLOWED:
        sys.exit(f"Name the sources to remove, from: {sorted(ALLOWED)}")
    total = time.time()
    with get_connection(autocommit=True) as conn:
        conn.execute("set statement_timeout = 0")
        conn.execute("set lock_timeout = '30s'")

        t = time.time()
        crossing = conn.execute("""select count(*) from employments e
            join companies c on c.company_id = e.company_id
            join people p on p.person_id = e.person_id
            where (c.source = any(%(o)s)) <> (p.source = any(%(o)s))""", {"o": OLD}).fetchone()[0]
        if crossing:
            sys.exit(f"Stopped, nothing deleted: {crossing} jobs link old and kept data.")
        cur = conn.execute(PLAN, (OLD,))
        names = [d.name for d in cur.description]
        plan = [dict(zip(names, r)) for r in cur.fetchall()]
        odd = [p["company_id"] for p in plan
               if not p["jobs"] == p["current_jobs"] == p["people"]]
        if odd:
            sys.exit(f"Stopped, nothing deleted: unexpected jobs at {odd[:5]}.")
        before = {n: conn.execute(f"select count(*) from {n}").fetchone()[0] for n in TABLES}
        final = {n: before[n] - (len(plan) if col is None else sum(p[col] for p in plan))
                 for n, col in TABLES.items()}
        keep = {s: n for s, n in conn.execute(
            "select source, count(*) from companies group by 1").fetchall() if s not in OLD}
        clock(f"checks and per-company counts ({len(plan)} companies)", t)

        t = time.time()
        if RECOVERY.exists():
            keys = json.loads(RECOVERY.read_text(encoding="utf-8"))
        else:
            keys = slow_keys(conn)
            if len(keys) != len(SLOW_KEYS):
                sys.exit(f"Stopped, nothing deleted: found {len(keys)} of {len(SLOW_KEYS)} keys.")
            RECOVERY.write_text(json.dumps(keys, indent=1), encoding="utf-8")
        for k in keys:
            conn.execute(f'alter table {k["table"]} drop constraint if exists "{k["name"]}"')
        clock("drop the 7 slow foreign keys", t)

        problem = None
        t = time.time()
        times, done = [], [0, 0, 0, 0, 0, 0]
        try:
            for i, p in enumerate(plan, 1):
                one = time.time()
                want = (p["sources"], p["lines"], p["answers"], p["jobs"], p["people"], 1)
                with conn.transaction():
                    got = conn.execute(DELETE_ONE, {"c": p["company_id"], "old": OLD}).fetchone()
                    if tuple(got) != want:
                        problem = f"{p['company_id']}: removed {tuple(got)}, expected {want}"
                        raise _Stop                 # leaving the block rolls this company back
                done = [a + b for a, b in zip(done, got)]
                times.append(time.time() - one)
                if i % 50 == 0 or i == len(plan):
                    print(f"{i}/{len(plan)} companies, {done[4]:,} people removed, "
                          f"{time.time() - t:.0f} s", flush=True)
        except _Stop:
            pass
        finally:
            if times:
                clock(f"delete {len(times)} companies (average {sum(times) / len(times):.2f} s, "
                      f"slowest {max(times):.2f} s each)", t)
            t = time.time()
            restore_keys(conn, keys)
            clock("add back and validate the 7 foreign keys", t)
        if problem:
            sys.exit(f"Stopped at {problem}. That company was rolled back; rerun continues.")

        t = time.time()
        counts = {n: conn.execute(f"select count(*) from {n}").fetchone()[0] for n in TABLES}
        left = dict(conn.execute("select source, count(*) from companies group by 1").fetchall())
        people = dict(conn.execute("select source, count(*) from people group by 1").fetchall())
        for n, want in final.items():
            print(f"{'ok  ' if counts[n] == want else 'FAIL'} {n:<16} {counts[n]:>7,} "
                  f"(expected {want:,})")
        print("companies kept:", left)
        print("people kept:", people)
        clock("final counts", t)
        print(f"removed: {done[0]} sources, {done[1]:,} lines, {done[2]:,} answer-key rows, "
              f"{done[3]:,} jobs, {done[4]:,} people, {done[5]} companies")
        if counts != final or left != keep or set(people) & set(OLD):
            sys.exit("FAILED: the final counts differ.")
    clock("whole removal", total)
    print("REMOVED: every company committed, and every foreign key validated.")


class _Stop(Exception):
    """A company's counts differ: roll it back and leave the loop."""


if __name__ == "__main__":
    main()
