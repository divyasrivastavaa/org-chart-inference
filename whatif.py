"""
whatif.py  -  "What if" changes for one app visitor, applied in memory only.

events = {"leavers": [...], "added": [...]}, kept in st.session_state by app.py. Nothing
is written to disk: the employee rows of the affected companies are copied, changed and
re-run through infer.predict(). The same leavers.apply() as the data builders is used,
so a what-if leaver looks exactly like a leaver from scripts/leavers.csv.
"""
import datetime

import pandas as pd

import infer
import leavers

LEAVER_COLUMNS = ["company_id", "full_name", "left_date", "new_company", "note"]


def empty():
    return {"leavers": [], "added": []}


def leaver(company_id, full_name, new_company=""):
    return {"company_id": company_id, "full_name": full_name,
            "left_date": datetime.date.today().isoformat(),
            "new_company": new_company or "", "note": "what-if"}


def person(company_id, full_name, title, department=""):
    return {"company_id": company_id, "full_name": full_name.strip(),
            "title": title.strip(), "department": (department or "").strip()}


def touched(emp, events):
    """IDs of all companies whose chart changes (old and new company of a leaver)."""
    by_name = dict(zip(emp.company_name.str.lower(), emp.company_id))
    out = {a["company_id"] for a in events["added"]}
    for lv in events["leavers"]:
        out.add(lv["company_id"])
        if lv["new_company"].lower() in by_name:
            out.add(by_name[lv["new_company"].lower()])
    return out


def next_id(ids, company_id):
    """R01-E014 -> R01-E015, keeping the dataset's number width (E001 or E0001)."""
    nums = ids[ids.str.startswith(company_id + "-E")].str.extract(r"-E(\d+)$")[0].dropna()
    return f"{company_id}-E{int(nums.astype(int).max()) + 1:0{len(nums.iloc[0])}d}"


def apply(emp, events):
    """Re-run inference for every company the events touch. Returns predictions for
    those companies only (empty frame when there are no events)."""
    companies = touched(emp, events)
    if not companies:
        return pd.DataFrame()
    sub = emp[emp.company_id.isin(companies)].copy()
    # 1. Added people first (so an added person could also be made to leave)
    for a in events["added"]:
        row = sub[sub.company_id == a["company_id"]].iloc[0].copy()
        row[:] = None
        info = sub[sub.company_id == a["company_id"]].iloc[0]
        row["company_id"], row["company_name"] = info.company_id, info.company_name
        row["industry"], row["size"] = info.industry, info["size"]
        row["employee_id"] = next_id(sub.employee_id, a["company_id"])
        row["full_name"], row["title"] = a["full_name"], a["title"]
        row["department"] = a["department"] or None
        if "source" in sub:
            row["source"] = "manually added"
        sub = pd.concat([sub, row.to_frame().T], ignore_index=True)
    # 2. Leavers: same logic as scripts/leavers.csv
    if events["leavers"]:
        lv = pd.DataFrame(events["leavers"], columns=LEAVER_COLUMNS)
        sub, _ = leavers.apply(sub, None, lv, log=lambda msg: None)
    # 3. Re-run the normal inference, in memory
    pred, _, _ = infer.predict(sub)
    return pred
