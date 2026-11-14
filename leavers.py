"""
leavers.py  -  people who leave a company (scripts/leavers.csv).

Columns: company_id, full_name, left_date, new_company (may be blank), note (optional).
Used by generate_data.py (synthetic) and scripts/build_real.py (real) when data is built:

1. The leaver is removed from their old company.
2. Their position stays as a VACANT card: same employee_id, their old title, and a note
   like "vacant since Nov 2026, left for Google". Keeping the same ID means everyone who
   reported to them now points at the vacant card (infer.py marks them "manager left").
3. If new_company is a company in the data, the person is added there with their title
   and a new ID, and infer.py places them like anyone else (no answer -> never scored).

With an empty leavers.csv nothing is changed (not even the columns), so the normal
results stay exactly the same.
"""
from pathlib import Path

import pandas as pd

LEAVERS_FILE = Path(__file__).resolve().parent / "scripts" / "leavers.csv"
VACANT_NAME = "Vacant position"


def load(path=LEAVERS_FILE):
    if not Path(path).exists():
        return pd.DataFrame()
    lv = pd.read_csv(path, dtype=str).fillna("")
    return lv[lv.full_name.str.strip() != ""]


def note_for(lv):
    when = pd.to_datetime(lv["left_date"]).strftime("%b %Y") if lv["left_date"] else "recently"
    note = f"vacant since {when}"
    if lv["new_company"].strip():
        note += f", left for {lv['new_company'].strip()}"
    if lv.get("note", "").strip():
        note += f" ({lv['note'].strip()})"
    return note


def apply(emp, truth=None, leavers=None, log=print):
    """emp = employees table; truth = answer key (synthetic only, else None).
    Returns (emp, truth) with leavers applied."""
    leavers = load() if leavers is None else leavers
    base = truth if truth is not None else emp            # clean titles live in truth
    found = []
    for _, lv in leavers.iterrows():
        if ((base.company_id == lv.company_id.strip())
                & (base.full_name == lv.full_name.strip())).any():
            found.append(lv)
        else:          # e.g. a real-data leaver while building synthetic data
            log(f"leavers: {lv.full_name} not found in {lv.company_id}, skipped")
    if not found:
        return emp, truth

    tables = {"emp": emp.copy()}
    if truth is not None:
        tables["truth"] = truth.copy()
    for t in tables.values():
        t["vacant"], t["vacancy_note"], t["former_holder"] = False, "", ""

    for lv in found:
        cid, name, new_co = lv.company_id.strip(), lv.full_name.strip(), lv.new_company.strip()
        b = tables["truth"] if truth is not None else tables["emp"]
        row = b[(b.company_id == cid) & (b.full_name == name)].iloc[0]
        eid, title = row.employee_id, row.title

        # 3. Joins another company in our data (decided BEFORE the old row is changed)
        target = b[b.company_name.str.lower() == new_co.lower()] if new_co else b.iloc[0:0]
        if new_co and target.empty:
            log(f"leavers: {new_co} is not in the data, {name} is only removed")
        elif new_co and (target.full_name == name).any():
            log(f"leavers: {name} is already in {new_co}, not added twice")
        elif new_co:
            t_info = target.iloc[0]
            nums = target.employee_id.str.extract(r"-E(\d+)$")[0].dropna()
            new_id = f"{t_info.company_id}-E{int(nums.astype(int).max()) + 1:0{len(nums.iloc[0])}d}"
            for key, t in tables.items():
                src = t[t.employee_id == eid].iloc[0].copy()
                src["company_id"], src["company_name"] = t_info.company_id, t_info.company_name
                src["industry"], src["employee_id"], src["title"] = t_info.industry, new_id, title
                if "true_manager_id" in t:                # unknown boss in the new company
                    src["true_manager_id"], src["dotted_manager_id"] = None, None
                    src["scenario"] = "joined"
                if "source" in t:
                    src["source"] = "moved in"
                t = pd.concat([t, src.to_frame().T], ignore_index=True)
                t.loc[t.company_id == t_info.company_id, "size"] = int(t_info["size"]) + 1
                tables[key] = t
            log(f"leavers: {name} added to {t_info.company_name} as {new_id}")

        # 1 + 2. Old company: the person goes, the position stays as a vacant card
        for key, t in tables.items():
            t = t[t.employee_id != eid + "-DUP"].copy()          # drop duplicate rows
            mine = t.employee_id == eid
            t.loc[mine, "full_name"], t.loc[mine, "title"] = VACANT_NAME, title
            t.loc[mine, "vacant"], t.loc[mine, "vacancy_note"] = True, note_for(lv)
            t.loc[mine, "former_holder"] = name
            tables[key] = t
        log(f"leavers: {name} left {cid}; {title} is now vacant ({note_for(lv)})")

    return tables["emp"], tables.get("truth")
