"""
scripts/load_db.py  -  Phase 2: database. Loads a SMALL sample of the existing CSV data.

Default sample: all 20 ASX/NZX companies, 2 UK organisations (U01, U02) and 10 Benchmark
companies (C001-C010, seed 42 in data/). Fills companies, people, employments (current
title, is_current = true, change_type = 'initial') and answer_key. No inference.

Safe to rerun: every insert is "on conflict do nothing", so a second run adds no rows.
New companies get needs_refresh = true (the table default): no org chart inferred yet.
Prints only row counts (never the database address or password).

Run from the repo root:  python scripts/load_db.py [--benchmark 10] [--uk 2]
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                     # so "db" can be imported
from db.connect import get_connection  # noqa: E402

# Employee count -> size band (the same bands PDL uses)
SIZE_BANDS = [(10, "1-10"), (50, "11-50"), (200, "51-200"), (500, "201-500"),
              (1000, "501-1000"), (5000, "1001-5000"), (10000, "5001-10000")]


def size_band(n):
    if pd.isna(n):
        return None
    return next((band for top, band in SIZE_BANDS if n <= top), "10001+")


def clean(v):
    """pandas NaN -> None (SQL NULL); numpy numbers -> plain Python."""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    return v.item() if hasattr(v, "item") else v


def read(folder, source, n_companies):
    emp = pd.read_csv(ROOT / folder / "employees.csv", low_memory=False)
    truth = pd.read_csv(ROOT / folder / "truth.csv", low_memory=False)
    keep = sorted(emp.company_id.unique())[:n_companies]          # C001.., U01.., R01..
    emp = emp[emp.company_id.isin(keep)]
    emp = emp[~emp.employee_id.str.endswith("-DUP")]               # duplicate rows: same person
    if "source" in emp:
        emp = emp[emp.source != "manually added"]
    truth = truth[truth.company_id.isin(keep) & truth.employee_id.isin(emp.employee_id)]

    cos = emp.drop_duplicates("company_id")
    companies = [(c.company_id, c.company_name, clean(c.industry), size_band(c.size),
                  "United Kingdom" if source == "uk_organogram" else None, source)
                 for c in cos.itertuples()]
    people = [(e.employee_id, clean(e.full_name), source) for e in emp.itertuples()]
    jobs = [(e.employee_id, e.company_id, clean(e.title), clean(e.department),
             clean(e.location), source) for e in emp.itertuples()]

    if source == "asx_nzx":
        # Only managers confirmed in a written source; a blank here means "no source",
        # not "top of company", so those rows are left out
        truth = truth[truth.true_manager_id.notna()]
    answers = [(t.company_id, t.employee_id, clean(t.true_manager_id),
                clean(getattr(t, "true_level", None)),
                {"benchmark": "generator", "uk_organogram": "published organogram"}.get(
                    source, clean(getattr(t, "evidence_type", None))),
                clean(getattr(t, "source_url", None)), clean(getattr(t, "scenario", None)))
               for t in truth.itertuples()]
    return companies, people, jobs, answers


def counts(conn):
    by_source = conn.execute("""
        select 'companies', source, count(*) from companies group by source
        union all select 'people', source, count(*) from people group by source
        union all select 'employments', source, count(*) from employments group by source
        union all select 'answer_key', c.source, count(*) from answer_key a
                  join companies c using (company_id) group by c.source
        order by 1, 2""").fetchall()
    totals = {t: conn.execute(f"select count(*) from {t}").fetchone()[0]       # fixed names
              for t in ["companies", "people", "employments", "reporting_lines", "runs",
                        "answer_key"]}
    return totals, by_source


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", type=int, default=10, help="Benchmark companies (seed 42)")
    ap.add_argument("--uk", type=int, default=2, help="UK organisations")
    args = ap.parse_args()
    plan = [("data/real", "asx_nzx", None), ("data/uk", "uk_organogram", args.uk),
            ("data", "benchmark", args.benchmark)]

    with get_connection() as conn:               # one transaction: all or nothing
        before, _ = counts(conn)
        for folder, source, n in plan:
            companies, people, jobs, answers = read(folder, source, n)
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into companies (company_id, name, industry, size_band, country, "
                    "source) values (%s, %s, %s, %s, %s, %s) "
                    "on conflict (company_id) do nothing", companies)
                cur.executemany(
                    "insert into people (person_id, full_name, source) values (%s, %s, %s) "
                    "on conflict (person_id) do nothing", people)
                cur.executemany(
                    "insert into employments (person_id, company_id, title, department, "
                    "location, source, is_current, change_type) "
                    "values (%s, %s, %s, %s, %s, %s, true, 'initial') "
                    "on conflict (person_id, company_id) where is_current do nothing", jobs)
                cur.executemany(
                    "insert into answer_key (company_id, person_id, true_manager_id, "
                    "true_level, evidence_type, source_url, scenario) "
                    "values (%s, %s, %s, %s, %s, %s, %s) "
                    "on conflict (company_id, person_id) do nothing", answers)
            print(f"{source:<14} read {len(companies):>3} companies, {len(people):>5} people, "
                  f"{len(answers):>5} answer-key rows")
        after, by_source = counts(conn)

    print("\nRows per table (new this run):")
    for t, n in after.items():
        print(f"  {t:<16} {n:>7,}  (+{n - before[t]:,})")
    print("\nRows per source:")
    for table, source, n in by_source:
        print(f"  {table:<12} {source:<14} {n:>7,}")


if __name__ == "__main__":
    main()
