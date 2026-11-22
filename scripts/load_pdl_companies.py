"""
scripts/load_pdl_companies.py  -  loads shortlisted real companies that are not in the database
yet, with their generated people, past jobs and answer key. No API calls, no inference.

All in one transaction (COPY into temporary tables, then insert): all or nothing. Stops before
committing if the database would pass the size limit. New companies get needs_refresh = true,
so `python refresh.py` infers exactly these. A second run adds nothing.
The same rows are appended to data/pdl_synth/ (employees.csv, truth.csv, past_jobs.csv), so
infer.py and evaluate.py can score them from files.

Run from the repo root:  python scripts/load_pdl_companies.py
"""
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import generate_pdl_people as gen  # noqa: E402
from db.connect import get_connection  # noqa: E402

LIMIT_BYTES = 450_000_000
OUT = ROOT / "data" / "pdl_synth"
SHORTLIST = ROOT / "data" / "pdl" / "company_shortlist.csv"
POOL = ROOT / "data" / "pdl" / "company_pool.csv"


def none(v):
    return None if v is None or (not isinstance(v, str) and pd.isna(v)) else v


def number(v):
    return None if pd.isna(v) else int(v)


def copy_rows(cur, table, columns, rows):
    with cur.copy(f"copy {table} ({', '.join(columns)}) from stdin") as copy:
        for row in rows:
            copy.write_row(row)


def main():
    short = pd.read_csv(SHORTLIST, keep_default_na=False)
    pool = pd.read_csv(POOL, keep_default_na=False, usecols=["id", "domain"])
    short = short.merge(pool, on="domain", how="left", validate="one_to_one")
    if short.id.isna().any() or (short.id == "").any():
        sys.exit("Stopped: a shortlisted company has no PDL id in the pool.")
    short["size_rank"] = [0 if s == "201-500" and gen.GROUP[i] == "Software" else 1
                          for s, i in zip(short["size"], short.industry)]

    with get_connection() as conn:
        have = dict(conn.execute("select source_ref, company_id from companies "
                                 "where source = 'pdl_company'").fetchall())
        used = [int(c[1:]) for c in have.values()]
        new = short[~short.id.isin(have)].sort_values(["size_rank", "domain"])
        if new.empty:
            print("Nothing to load: every shortlisted company is already in the database.")
            return
        first = max(used, default=0) + 1
        ids = {r: f"N{first + i:03d}" for i, r in enumerate(new.id)}
        short["company_id"] = short.id.map({**have, **ids})
        records = short.to_dict("records")
        new_records = [r for r in records if r["id"] in ids]
        truth, live, jobs = gen.generate(new_records, records)

        info = {r["company_id"]: r for r in new_records}
        with conn.cursor() as cur:
            run_id = cur.execute(
                "insert into runs (trigger, status, note, code_version) values "
                "('manual', 'running', 'load: PDL companies with generated people', %s) "
                "returning run_id", (subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                                    capture_output=True, text=True,
                                                    cwd=ROOT).stdout.strip() or None,)
            ).fetchone()[0]
            copy_rows(cur, "companies",
                      ["company_id", "name", "industry", "size_band", "country", "website",
                       "source", "source_ref"],
                      ((c, gen.nice_name(r["name"]), gen.display.proper_title(r["industry"]),
                        r["size"], gen.COUNTRY[r["country"]], r["domain"], "pdl_company", r["id"])
                       for c, r in info.items()))
            copy_rows(cur, "people", ["person_id", "full_name", "source", "source_ref"],
                      ((p, n, "generated", p) for p, n in zip(live.employee_id, live.full_name)))
            cur.execute("create temp table tmp_past (person_id text, company_id text, title text, "
                        "department text, location text, start_date date, end_date date) "
                        "on commit drop")
            copy_rows(cur, "tmp_past",
                      ["person_id", "company_id", "title", "department", "location", "start_date",
                       "end_date"],
                      ((j.employee_id, j.past_company_id, j.past_title, j.department,
                        none(j.location), j.start_date, j.end_date) for j in jobs.itertuples()))
            cur.execute("""insert into employments (person_id, company_id, title, department,
                               location, start_date, end_date, is_current, change_type, source)
                           select person_id, company_id, title, department, location, start_date,
                                  end_date, false, 'initial', 'generated' from tmp_past""")
            cur.execute("create temp table tmp_now (person_id text, company_id text, title text, "
                        "department text, location text, start_date date, change_type text) "
                        "on commit drop")
            copy_rows(cur, "tmp_now",
                      ["person_id", "company_id", "title", "department", "location", "start_date",
                       "change_type"],
                      ((p.employee_id, p.company_id, none(p.title), none(p.department),
                        none(p.location), p.start_date, p.change_type)
                       for p in live.itertuples()))
            cur.execute("""insert into employments (person_id, company_id, title, department,
                               location, start_date, is_current, change_type,
                               previous_employment_id, source)
                           select n.person_id, n.company_id, n.title, n.department, n.location,
                                  n.start_date, true, n.change_type, o.employment_id, 'generated'
                           from tmp_now n
                           left join employments o on o.person_id = n.person_id
                                and o.source = 'generated' and not o.is_current""")
            copy_rows(cur, "answer_key",
                      ["company_id", "person_id", "true_manager_id", "true_level", "evidence_type",
                       "scenario"],
                      ((p.company_id, p.employee_id, none(p.true_manager_id), int(p.true_level),
                        "generator", p.scenario) for p in live.itertuples()))
            size = cur.execute("select pg_database_size(current_database())").fetchone()[0]
            print(f"database inside the load: {size / 1e6:.1f} MB ({size / 1048576:.1f} MiB)")
            if size > LIMIT_BYTES:
                conn.rollback()
                sys.exit(f"Stopped and rolled back: {size / 1e6:.1f} MB is above the limit.")
            cur.execute("update runs set finished_at = now(), status = 'succeeded', "
                        "companies_processed = %s, people_processed = %s, cost_usd = 0 "
                        "where run_id = %s", (len(info), len(live), run_id))
        conn.commit()
        size = conn.execute("select pg_database_size(current_database())").fetchone()[0]

    OUT.mkdir(parents=True, exist_ok=True)
    keep = ["company_id", "company_name", "industry", "size", "employee_id", "full_name", "title",
            "department", "location"]
    answer = ["company_id", "employee_id", "true_manager_id", "true_level", "true_function",
              "true_team", "scenario", "dotted_manager_id", "in_dataset", "employment_type",
              "seniority", "junk_kind"]
    for name, df in (("employees.csv", live[keep]), ("truth.csv", truth[answer]),
                     ("past_jobs.csv", jobs)):
        path = OUT / name
        df.to_csv(path, mode="a" if path.exists() and used else "w",
                  header=not (path.exists() and used), index=False, encoding="utf-8")
    print(f"loaded {len(info)} companies ({ids[new.id.iloc[0]]} to {ids[new.id.iloc[-1]]}), "
          f"{len(live):,} people, {len(jobs):,} past jobs, {len(live):,} answer-key rows")
    print(f"database after the load: {size / 1e6:.1f} MB ({size / 1048576:.1f} MiB)")


if __name__ == "__main__":
    main()
