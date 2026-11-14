"""Add new people to one real company (on a COPY of the data) and see who the model
says they report to.

Usage:   python scripts/add_people.py R10 scripts/new_people_example.csv
The CSV needs the columns: full_name, title, department (department may be blank).

data/real/employees.csv is never changed. Each run starts from a fresh copy in
data/real_added/, so people added in earlier runs do not pile up.
"""
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent          # the repo folder
sys.path.insert(0, str(ROOT))                          # so "import infer" finds infer.py
import infer  # noqa: E402

SOURCE = ROOT / "data" / "real"
TARGET = ROOT / "data" / "real_added"


def main(company_id, people_csv):
    # 1. Read the new people and check the file has the columns we need
    new = pd.read_csv(people_csv, dtype=str).fillna("")
    missing = {"full_name", "title", "department"} - set(new.columns)
    if missing:
        sys.exit(f"{people_csv} is missing column(s): {', '.join(sorted(missing))}")

    # 2. Copy the original file; from here on we only touch the copy
    TARGET.mkdir(parents=True, exist_ok=True)
    shutil.copy(SOURCE / "employees.csv", TARGET / "employees.csv")
    emp = pd.read_csv(TARGET / "employees.csv", dtype=str)

    # 3. Check the company exists, and that nobody is added twice
    company = emp[emp.company_id == company_id]
    if company.empty:
        sys.exit(f"No company {company_id}. Choose one of: {', '.join(emp.company_id.unique())}")
    clash = set(new.full_name) & set(company.full_name)
    if clash:
        sys.exit(f"Already in {company_id}: {', '.join(sorted(clash))}")
    if new.full_name.duplicated().any():
        sys.exit(f"The same name appears twice in {people_csv}")

    # 4. New unique IDs: continue numbering after the company's highest one (R10-E010 -> E011)
    last = company.employee_id.str.extract(r"-E(\d+)$")[0].astype(int).max()
    new["employee_id"] = [f"{company_id}-E{last + i:03d}" for i in range(1, len(new) + 1)]

    # 5. Fill the company columns from the existing rows, then add the new rows
    info = company.iloc[0]
    new["company_id"] = company_id
    new["company_name"] = info.company_name
    new["industry"] = info.industry
    new["location"] = ""
    emp = pd.concat([emp, new[emp.columns.drop("size")]], ignore_index=True)
    emp.loc[emp.company_id == company_id, "size"] = str((emp.company_id == company_id).sum())
    emp.to_csv(TARGET / "employees.csv", index=False)

    # 6. Run the normal inference on the copy (writes data/real_added/predictions.csv)
    infer.main(TARGET)

    # 7. Print each new person with their predicted manager and confidence
    pred = pd.read_csv(TARGET / "predictions.csv")
    name_of = dict(zip(pred.employee_id, pred.full_name + " (" + pred.title + ")"))
    print(f"\nNew people in {info.company_name} ({company_id}):")
    for r in pred[pred.employee_id.isin(new.employee_id)].itertuples():
        if pd.notna(r.manager_id):
            boss = f"reports to {name_of[r.manager_id]}, confidence {r.confidence:.2f}"
        else:
            boss = f"no manager drawn ({r.reason})"
        print(f"  {r.employee_id}  {r.full_name} - {r.title}\n      -> {boss}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: python scripts/add_people.py COMPANY_ID people.csv")
    main(sys.argv[1], sys.argv[2])
