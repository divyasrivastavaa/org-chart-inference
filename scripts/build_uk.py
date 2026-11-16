"""Build data/uk/ (employees.csv + truth.csv) from the raw UK government organograms in
data/raw/uk/ (downloaded by scripts/download_uk.py). Works offline.
Also adds one row per organisation to data/real/sources.csv.

Contains public sector information licensed under the Open Government Licence v3.0.

employees.csv = what the model may see: name (or "Name withheld N"), job title and unit.
  NOT included: "Reports to Senior Post" (that IS the answer), grade, salary, pay band,
  FTE, contact details. Post references are replaced by new IDs in shuffled order, so an
  ID can't hint at the hierarchy.
truth.csv = the answer key: each post's manager from "Reports to Senior Post"; the top
  post ("XX") has none. Vacant, eliminated and "Not in post" posts are dropped. People
  whose manager post was dropped keep it as their true manager (not in the data, so the
  right answer is "no line"): scenario "manager_vacant".
Run:  python scripts/build_uk.py
"""
import csv
import random
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data" / "raw" / "uk", ROOT / "data" / "uk"
SOURCES = ROOT / "data" / "real" / "sources.csv"
DATASET = "UK government organograms"
SRC_COLS = ["dataset", "company_id", "company_name", "url", "what_it_shows", "date_checked",
            "licence"]


def read_raw(path):
    try:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    except UnicodeDecodeError:                         # some files are Windows-1252
        df = pd.read_csv(path, dtype=str, encoding="cp1252")
    df.columns = [c.strip() for c in df.columns]
    df = df[df["Post Unique Reference"].notna()].copy()
    for c in ["Post Unique Reference", "Name", "Job Title", "Unit", "Reports to Senior Post"]:
        df[c] = df[c].fillna("").str.strip()
    return df


def build_one(cid, org, df):
    name, title = df["Name"].str.lower(), df["Job Title"].str.lower()
    gone = name.isin(["vacant", "eliminated"]) | (title == "not in post")
    ref_parent = dict(zip(df["Post Unique Reference"], df["Reports to Senior Post"]))

    def depth(ref):                                    # top post = level 1
        d, seen = 1, set()
        while ref_parent.get(ref, "XX").upper() != "XX" and ref not in seen:
            seen.add(ref)
            ref, d = ref_parent[ref], d + 1
        return d

    keep = df[~gone].copy()
    order = list(range(len(keep)))
    random.Random(cid).shuffle(order)                  # IDs in shuffled order: no hint
    keep["employee_id"] = [f"{cid}-E{i + 1:03d}" for i in order]
    new_id = dict(zip(keep["Post Unique Reference"], keep["employee_id"]))
    dropped = set(df.loc[gone, "Post Unique Reference"])

    withheld = 0
    emp, truth = [], []
    for rec in keep.sort_values("employee_id").to_dict("records"):
        nm = rec["Name"]
        if nm.upper() in ("N/D", "") or nm.lower() == "n/a":
            withheld += 1
            nm = f"Name withheld {withheld}"           # numbered: two withheld Deputy Directors
                                                       # in one unit are still two people
        boss = rec["Reports to Senior Post"]
        if boss.upper() == "XX":
            mgr, scenario = None, "top"
        elif boss in dropped:
            mgr, scenario = f"{cid}-vacant-{boss}", "manager_vacant"
        else:
            mgr, scenario = new_id[boss], "normal"
        emp.append([cid, org, "Government", len(keep), rec["employee_id"], nm,
                    rec["Job Title"], rec["Unit"], ""])
        truth.append([cid, org, rec["employee_id"], nm, rec["Job Title"], mgr,
                      depth(rec["Post Unique Reference"]), scenario, True, None,
                      rec.get("Grade (or equivalent)", "")])
    return emp, truth, int(gone.sum())


def main():
    manifest = pd.read_csv(RAW / "manifest.csv", dtype=str)
    OUT.mkdir(parents=True, exist_ok=True)
    emp_all, truth_all, src = [], [], []
    for m in manifest.itertuples():
        df = read_raw(RAW / m.file)
        emp, truth, n_gone = build_one(m.company_id, m.organisation, df)
        emp_all += emp
        truth_all += truth
        as_at = m.url.rsplit("Z-", 1)[-1][:10]         # ".../2026-..Z-2026-09-30-organogram-senior.csv"
        src.append([DATASET, m.company_id, m.organisation, m.url,
                    f"Senior staff organogram (as at {as_at}): {len(emp)} posts with "
                    f"job title, unit and reporting line", m.downloaded, m.licence])
        print(f"{m.company_id} {m.organisation}: {len(emp)} posts "
              f"({n_gone} vacant / eliminated / not in post dropped)")
    pd.DataFrame(emp_all, columns=["company_id", "company_name", "industry", "size",
                                   "employee_id", "full_name", "title", "department",
                                   "location"]).to_csv(OUT / "employees.csv", index=False)
    pd.DataFrame(truth_all, columns=["company_id", "company_name", "employee_id",
                                     "full_name", "title", "true_manager_id", "true_level",
                                     "scenario", "in_dataset", "dotted_manager_id",
                                     "grade"]).to_csv(OUT / "truth.csv", index=False)

    # data/real/sources.csv: replace this dataset's rows, keep everyone else's
    rows = []
    if SOURCES.exists():
        with open(SOURCES, encoding="utf-8") as f:
            rows = [[r.get(c, "") for c in SRC_COLS] for r in csv.DictReader(f)
                    if r.get("dataset") != DATASET]
    SOURCES.parent.mkdir(parents=True, exist_ok=True)
    with open(SOURCES, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([SRC_COLS] + rows + src)
    print(f"{len(emp_all)} posts in {len(manifest)} organisations; "
          f"{len(src)} rows added to {SOURCES.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
