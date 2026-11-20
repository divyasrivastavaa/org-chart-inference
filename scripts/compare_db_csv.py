"""
scripts/compare_db_csv.py  -  Phase 2: database. Read only.
Precision and coverage from the database (current reporting_lines vs answer_key) next to
the CSV results (predictions.csv vs truth.csv) for exactly the same companies.
Scored people: those with a known true manager (as in evaluate.py; Benchmark people
missing from the data and ASX/NZX people without a written source are not scored).
Also: lines drawn at the PDL companies (no answer key) and the last runs.
Run from the repo root:  python scripts/compare_db_csv.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db.connect import get_connection  # noqa: E402

FOLDERS = {"benchmark": "data", "asx_nzx": "data/real", "uk_organogram": "data/uk"}


def query(conn, sql, *params):
    cur = conn.execute(sql, params or None)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def score(frame):
    given = frame.manager_id.notna()
    right = given & (frame.manager_id == frame.true_manager_id)
    return pd.Series({"people": len(frame), "drawn": int(given.sum()),
                      "right": int(right.sum()), "wrong": int((given & ~right).sum()),
                      "precision": right.sum() / max(given.sum(), 1),
                      "coverage": given.mean()})


with get_connection() as conn:
    conn.read_only = True
    db = query(conn, """
        select c.source, a.company_id, a.person_id as employee_id, a.true_manager_id,
               rl.manager_id
        from answer_key a
        join companies c using (company_id)
        left join reporting_lines rl on rl.person_id = a.person_id
                                    and rl.company_id = a.company_id and rl.is_current
        where a.true_manager_id is not null""")
    pdl = query(conn, """
        select c.name, count(*) as people, count(rl.manager_id) as lines_drawn,
               count(rl.proposed_manager_id) - count(rl.manager_id) as below_threshold,
               count(*) - count(rl.proposed_manager_id) as no_candidate
        from reporting_lines rl join companies c using (company_id)
        where rl.is_current and c.source = 'pdl' group by c.name""")
    reasons = query(conn, """
        select coalesce(rl.reason, '(line drawn)') as reason, count(*) as people
        from reporting_lines rl join companies c using (company_id)
        where rl.is_current and c.source = 'pdl' group by 1 order by 2 desc""")
    runs = query(conn, """
        select run_id, trigger, status, finished_at - started_at as duration,
               companies_processed, people_processed, lines_written, cost_usd
        from runs order by run_id""")

rows, per_company = [], []
for source, folder in FOLDERS.items():
    mine = db[db.source == source]
    truth = pd.read_csv(ROOT / folder / "truth.csv", low_memory=False)
    pred = pd.read_csv(ROOT / folder / "predictions.csv", low_memory=False)
    truth = truth[truth.company_id.isin(mine.company_id) & truth.true_manager_id.notna()]
    if "in_dataset" in truth:
        truth = truth[truth.in_dataset.astype(bool)]
    csv = truth[["company_id", "employee_id", "true_manager_id"]].merge(
        pred[["employee_id", "manager_id"]], on="employee_id", how="left")
    for name, frame in [("database", mine), ("CSV", csv)]:
        rows.append({"dataset": source, "from": name, **score(frame)})
    for cid in sorted(mine.company_id.unique()):          # companies where the two differ
        a, b = score(mine[mine.company_id == cid]), score(csv[csv.company_id == cid])
        if (a.drawn, a.right, a.people) != (b.drawn, b.right, b.people):
            per_company.append({"company": cid, "people (db/csv)": f"{a.people}/{b.people}",
                                "drawn (db/csv)": f"{a.drawn}/{b.drawn}",
                                "right (db/csv)": f"{a.right}/{b.right}"})

table = pd.DataFrame(rows)
table["precision"] = table.precision.map("{:.1%}".format)
table["coverage"] = table.coverage.map("{:.1%}".format)
print("Precision and coverage, same companies (20 ASX/NZX, U01-U02, C001-C010):\n")
print(table.to_string(index=False))
print("\nCompanies where database and CSV differ:")
print(pd.DataFrame(per_company).to_string(index=False) if per_company else "  none")
print("\nPDL companies (no answer key):")
print(pdl.to_string(index=False))
print("\n" + reasons.to_string(index=False))
print("\nRuns:")
print(runs.to_string(index=False))
