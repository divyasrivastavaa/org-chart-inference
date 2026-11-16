"""
whatif.py  -  "What if" changes for one app visitor, applied in memory only.

events = {"leavers": [...], "added": [...], "new_orgs": [...]}, kept in st.session_state by
app.py. Nothing is written to disk: the employee rows of the affected companies are
copied, changed and re-run through infer.predict(), the normal inference.

- Added people get the next free ID and the tag "manually added". They have no answer,
  so they are never scored.
- Leavers are taken out before inference, so the people under them are re-placed by the
  normal rules. Each leaver comes back afterwards as a faded "left" card at their old
  place, and their former direct reports are labelled "was under <leaver>".
- A former report that the rules can't place, and who has no one more senior left in
  their function, goes under the CEO as "interim" (confidence INTERIM_CONFIDENCE, never
  scored). No single CEO -> they keep waiting for a new manager.
- A leaver whose "moving to" is a company in the same data is added there with their title.
(scripts/leavers.csv, used when data is built, still uses leavers.py: vacant cards.)
"""
import pandas as pd

import infer

# Lower than a normal line (0.98), but above the default threshold 0.7 so it is drawn
INTERIM_CONFIDENCE = 0.75


def empty():
    return {"leavers": [], "added": [], "new_orgs": []}


def leaver(company_id, employee_id, full_name, moving_to, former_manager, reports,
           judge="rules"):
    """former_manager / reports: the leaver's manager and direct reports on the chart at
    the moment they were removed (so the faded card stays where it was).
    judge: "rules" or "gemini" = who re-places the reports."""
    return {"company_id": company_id, "employee_id": employee_id, "full_name": full_name,
            "new_company": (moving_to or "").strip(), "former_manager": former_manager,
            "reports": list(reports), "judge": judge}


def person(company_id, full_name, title, department="", company_name=None, judge="rules"):
    """company_name is only needed for a new organisation (not in the data yet).
    judge: "rules" or "gemini" = who places this person."""
    return {"company_id": company_id, "full_name": full_name.strip(),
            "title": title.strip(), "department": (department or "").strip(),
            "company_name": company_name, "judge": judge}


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
    if nums.empty:                                   # a new organisation
        return f"{company_id}-E001"
    return f"{company_id}-E{int(nums.astype(int).max()) + 1:0{len(nums.iloc[0])}d}"


def add_row(sub, company_id, full_name, title, department, tag, company_name=None):
    same = sub[sub.company_id == company_id]
    row = pd.Series(None, index=sub.columns, dtype=object)
    if len(same):
        info = same.iloc[0]
        row["company_name"], row["industry"], row["size"] = (info.company_name,
                                                             info.industry, info["size"])
    else:                                            # a new organisation
        row["company_name"], row["industry"] = company_name, "New organisation"
    row["company_id"], row["employee_id"] = company_id, next_id(sub.employee_id, company_id)
    row["full_name"], row["title"], row["department"] = full_name, title, department or None
    row["tag"] = tag
    if "source" in sub:
        row["source"] = "manually added"             # real data: never scored
    return pd.concat([sub, row.to_frame().T], ignore_index=True)


def apply(emp, events, judge=None):
    """Re-run inference for every company the events touch. Returns predictions for
    those companies only (empty frame when there are no events).
    judge: a judge_gemini.GeminiJudge, or None (no key). People whose change asked for
    Gemini are placed by it; if it is missing or fails, the rules' answer stays and the
    "judge" column says why."""
    companies = touched(emp, events)
    if not companies:
        return pd.DataFrame()
    sub = emp[emp.company_id.isin(companies)].copy()
    sub["tag"] = None
    # 1. Added people first (so an added person could also be made to leave)
    for a in events["added"]:
        sub = add_row(sub, a["company_id"], a["full_name"], a["title"], a["department"],
                      "manually added", a.get("company_name"))
    # 2. Leavers moving to a company in this data: added there with their title
    by_name = dict(zip(emp.company_name.str.lower(), emp.company_id))
    for lv in events["leavers"]:
        target = by_name.get(lv["new_company"].lower())
        if target and target != lv["company_id"]:
            title = sub.loc[sub.employee_id == lv["employee_id"], "title"].iloc[0]
            old = sub.loc[sub.employee_id == lv["employee_id"], "company_name"].iloc[0]
            sub = add_row(sub, target, lv["full_name"], title, None, f"joined from {old}")
    # 3. Take leavers out (and their duplicate rows), re-run the normal inference
    gone_ids = {lv["employee_id"] for lv in events["leavers"]}
    is_gone = sub.employee_id.isin(gone_ids | {e + "-DUP" for e in gone_ids})
    gone = sub[sub.employee_id.isin(gone_ids)].copy()
    pred, _, _ = infer.predict(sub[~is_gone].copy())
    # 4. Leavers come back as faded cards at their old place
    for lv in events["leavers"]:
        mine = gone.employee_id == lv["employee_id"]
        gone.loc[mine, "left_to"] = lv["new_company"]
        gone.loc[mine, "former_manager"] = lv["former_manager"]
    if len(gone):
        parsed = gone.apply(lambda r: infer.parse_title(r.title, r.department), axis=1)
        gone["level"] = [p[0] for p in parsed]
        gone["function"] = [p[1] for p in parsed]
        gone["left"], gone["confidence"], gone["reason"] = True, 0.0, "left the company"
        pred = pd.concat([pred, gone], ignore_index=True)
    # 5. Former direct reports: "manager left · was under <name>"
    pred["was_under"] = None
    for lv in events["leavers"]:
        mine = pred.employee_id.isin(lv["reports"]) & (pred.get("left") != True)  # noqa: E712
        pred.loc[mine, "was_under"] = lv["full_name"]
    # 6. Interim under the CEO: the rules found no manager AND nobody more senior is left in
    #    the person's function (e.g. the CTO left: the engineering VPs have no one above)
    pred["interim"] = False
    here = pred.left.astype(str) != "True" if "left" in pred else pd.Series(True, pred.index)
    for cid, co in pred[here].groupby("company_id"):
        ceos = co.employee_id[co.reason == "top of company"]
        if len(ceos) != 1:                            # no CEO (or two): keep waiting
            continue
        for r in co[co.was_under.notna() & co.pred_manager_id.isna()].itertuples():
            if not isinstance(r.function, str) or pd.isna(r.level) or r.level == 1:
                continue                              # unreadable title, or the CEO
            senior = co[(co.function == r.function) & (co.level < r.level)
                        & (co.employee_id != r.employee_id)]
            if senior.empty:
                pred.loc[pred.employee_id == r.employee_id,
                         ["pred_manager_id", "confidence", "interim", "reason"]] = \
                    [ceos.iloc[0], INTERIM_CONFIDENCE, True,
                     f"interim: no one more senior left in {r.function}"]
    # 7. Gemini, for the people whose change asked for it (added people, leavers' reports)
    added_ids = {(r.company_id, r.full_name): r.employee_id
                 for r in pred[pred.tag == "manually added"].itertuples()}
    wanted = {added_ids.get((a["company_id"], a["full_name"])) for a in events["added"]
              if a.get("judge") == "gemini"}
    for lv in events["leavers"]:
        if lv.get("judge") == "gemini":
            wanted |= set(lv["reports"])
    here = pred.left.astype(str) != "True" if "left" in pred else pd.Series(True, pred.index)
    wanted = set(pred.employee_id[pred.employee_id.isin(wanted) & here])
    pred["judge"] = None
    if wanted:
        import judge_gemini                       # only needed when Gemini was chosen
        mine = pred.employee_id.isin(wanted)
        if judge is None:
            pred.loc[mine, "judge"] = "rules (no Gemini API key set)"
        else:
            try:
                answers = judge_gemini.place(pred[here], wanted, judge)
            except judge_gemini.JudgeError as e:
                pred.loc[mine, "judge"] = f"rules (Gemini failed: {e})"
            else:
                for eid, a in answers.items():
                    row = pred.employee_id == eid
                    pred.loc[row, ["pred_manager_id", "confidence", "reason", "judge"]] = \
                        [a["manager_id"], a["confidence"], "Gemini: " + a["reason"], "Gemini"]
                    if "interim" in pred:
                        pred.loc[row, "interim"] = False
    return pred
