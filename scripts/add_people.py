"""Add new people to one real company and see who the model says they report to.

Try it out (temporary):
    python scripts/add_people.py R10 scripts/new_people_example.csv
    -> works on a fresh copy in data/real_added/. data/real is NOT changed.

Keep them for good:
    python scripts/add_people.py R10 scripts/new_people_example.csv --save
    -> appends them to scripts/added_people.csv, rebuilds data/real (build_real.py and
       build_real_truth.py) and reruns infer.py on data/real. From then on they are part
       of the real data everywhere, marked source = "manually added", and never scored.

The CSV needs the columns: full_name, title, department (department may be blank).
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent          # the repo folder
sys.path.insert(0, str(ROOT))                          # so "import infer" finds infer.py
import infer  # noqa: E402

SOURCE = ROOT / "data" / "real"
TARGET = ROOT / "data" / "real_added"
ADDED_FILE = ROOT / "scripts" / "added_people.csv"


def read_new_people(people_csv):
    new = pd.read_csv(people_csv, dtype=str).fillna("")
    missing = {"full_name", "title", "department"} - set(new.columns)
    if missing:
        sys.exit(f"{people_csv} is missing column(s): {', '.join(sorted(missing))}")
    new["full_name"] = new.full_name.str.strip()
    if new.full_name.duplicated().any():
        sys.exit(f"The same name appears twice in {people_csv}")
    return new


def check_company(emp, company_id, new):
    company = emp[emp.company_id == company_id]
    if company.empty:
        sys.exit(f"No company {company_id}. Choose one of: {', '.join(emp.company_id.unique())}")
    clash = set(new.full_name) & set(company.full_name)
    if clash:
        sys.exit(f"Already in {company_id}: {', '.join(sorted(clash))}")
    return company


def temporary_copy(company_id, new):
    """Old behaviour: add the people to a copy in data/real_added/ only."""
    TARGET.mkdir(parents=True, exist_ok=True)
    shutil.copy(SOURCE / "employees.csv", TARGET / "employees.csv")
    emp = pd.read_csv(TARGET / "employees.csv", dtype=str)
    company = check_company(emp, company_id, new)
    last = company.employee_id.str.extract(r"-E(\d+)$")[0].astype(int).max()
    new["employee_id"] = [f"{company_id}-E{last + i:03d}" for i in range(1, len(new) + 1)]
    info = company.iloc[0]
    new["company_id"], new["company_name"], new["industry"] = company_id, info.company_name, info.industry
    new["location"], new["source"] = "", "manually added"
    emp = pd.concat([emp, new[emp.columns.drop("size")]], ignore_index=True)
    emp.loc[emp.company_id == company_id, "size"] = str((emp.company_id == company_id).sum())
    emp.to_csv(TARGET / "employees.csv", index=False)
    infer.main(TARGET)
    return TARGET, list(new.employee_id)


def save_permanently(company_id, new):
    """--save: append to scripts/added_people.csv, rebuild data/real, rerun inference."""
    emp = pd.read_csv(SOURCE / "employees.csv", dtype=str)
    check_company(emp, company_id, new)
    rows = new[["full_name", "title", "department"]].assign(company_id=company_id)
    rows = rows[["company_id", "full_name", "title", "department"]]
    rows.to_csv(ADDED_FILE, mode="a", header=not ADDED_FILE.exists(), index=False)
    print(f"Saved {len(rows)} people to {ADDED_FILE.relative_to(ROOT)}")
    for script in ["build_real.py", "build_real_truth.py"]:
        subprocess.run([sys.executable, str(ROOT / "scripts" / script)], check=True)
    infer.main(SOURCE)
    emp = pd.read_csv(SOURCE / "employees.csv", dtype=str)
    mine = emp[(emp.company_id == company_id) & emp.full_name.isin(new.full_name)
               & (emp.source == "manually added")]
    return SOURCE, list(mine.employee_id)


def show(folder, ids):
    pred = pd.read_csv(folder / "predictions.csv")
    name_of = dict(zip(pred.employee_id, pred.full_name + " (" + pred.title + ")"))
    rows = pred[pred.employee_id.isin(ids)]
    print(f"\nNew people in {rows.company_name.iloc[0]} ({rows.company_id.iloc[0]}):")
    for r in rows.itertuples():
        if pd.notna(r.manager_id):
            boss = f"reports to {name_of[r.manager_id]}, confidence {r.confidence:.2f}"
        else:
            boss = f"no manager drawn ({r.reason})"
        print(f"  {r.employee_id}  {r.full_name} - {r.title}\n      -> {boss}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--save"]
    if len(args) != 2:
        sys.exit("Usage: python scripts/add_people.py COMPANY_ID people.csv [--save]")
    company_id, people = args
    new_people = read_new_people(people)
    if "--save" in sys.argv:
        show(*save_permanently(company_id, new_people))
    else:
        show(*temporary_copy(company_id, new_people))
