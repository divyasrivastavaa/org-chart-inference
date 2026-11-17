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
- A title change keeps the person and re-places them by the normal rules. With "leave old
  position vacant", a dashed vacant card keeps their old place and their old reports stay
  under it. Without it, the reports stay under the person if that still fits (same
  function, still more senior), otherwise they are re-placed like a leaver's team.
(scripts/leavers.csv, used when data is built, still uses leavers.py: vacant cards.)
"""
import pandas as pd

import infer

# Lower than a normal line (0.98), but above the default threshold 0.7 so it is drawn
INTERIM_CONFIDENCE = 0.75


def empty():
    return {"leavers": [], "added": [], "new_orgs": [], "changes": []}


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


def change(company_id, employee_id, full_name, old_title, new_title, vacant, old, new,
           old_manager, old_manager_name, old_confidence, reports):
    """A title change. old / new = (level, function) as the rules read them in this
    company. old_manager, old_confidence and reports ({id: confidence}) are the chart at
    the moment of the change, so a vacant card can keep the old place."""
    kind = ("change" if pd.isna(old[0]) or pd.isna(new[0])
            else "promotion" if new[0] < old[0] else "demotion" if new[0] > old[0]
            else "move")
    num = lambda x: None if pd.isna(x) else int(x)     # noqa: E731  (plain ints for undo)
    return {"company_id": company_id, "employee_id": employee_id, "full_name": full_name,
            "old_title": old_title if isinstance(old_title, str) else None,
            "new_title": new_title.strip(), "vacant": bool(vacant),
            "old_level": num(old[0]), "new_level": num(new[0]),
            "old_function": old[1], "new_function": new[1], "kind": kind,
            "old_manager": old_manager if isinstance(old_manager, str) else None,
            "old_manager_name": old_manager_name,
            "old_confidence": float(old_confidence) if pd.notna(old_confidence) else 0.0,
            "reports": {k: float(v) for k, v in reports.items()}}


def in_context(co, employee_id=None, new_title=None):
    """Level and function of everyone in one company as the rules see them (title +
    company context), optionally with one person's title replaced. Indexed by employee_id."""
    co = co[["employee_id", "company_name", "title", "department"]].copy()
    if employee_id is not None:
        co.loc[co.employee_id == employee_id, "title"] = new_title
    parsed = co.apply(lambda r: infer.parse_title(r.title, r.department), axis=1)
    co[["level", "function", "team", "is_contractor"]] = pd.DataFrame(parsed.tolist(),
                                                                      index=co.index)
    for c in ("team", "function"):
        co[c] = co[c].astype(object).where(co[c].notna(), None)
    return infer.fix_with_company_context(co).set_index("employee_id")[["level", "function"]]


def check_title(co, employee_id, new_title):
    """None if the new title can be used, else the warning to show.
    co = the people working in this company now."""
    new = new_title.strip()
    old = co.loc[co.employee_id == employee_id, "title"].iloc[0]
    if isinstance(old, str) and (old.strip().lower() == new.lower()
                                 or infer.normalise(old) == infer.normalise(new)):
        return "The new title is the same as the old one."
    level, function = infer.parse_title(new, None)[:2]
    ctx = in_context(co, employee_id, new)
    if (level == 7 and function is None) or pd.isna(ctx.level[employee_id]) \
            or ctx.function[employee_id] is None:
        return (f"\"{new}\" can't be read by our rules (no level or department we know). "
                "Try a title like \"VP Engineering\" or \"Marketing Manager\".")
    others = ctx[(ctx.level == 1) & (ctx.index != employee_id)]
    if ctx.level[employee_id] == 1 and len(others):
        name = co.set_index("employee_id").full_name[others.index[0]]
        return (f"\"{new}\" would make a second CEO, but {name} already runs this company. "
                "Change their title first.")
    return None


def touched(emp, events):
    """IDs of all companies whose chart changes (old and new company of a leaver)."""
    by_name = dict(zip(emp.company_name.str.lower(), emp.company_id))
    out = {a["company_id"] for a in events["added"]}
    out |= {c["company_id"] for c in events.get("changes", [])}
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
    # 2b. Title changes, in order (also on the person's exact duplicate row, if any)
    changes = events.get("changes", [])
    for ch in changes:
        rows = sub.employee_id.isin([ch["employee_id"], ch["employee_id"] + "-DUP"])
        sub.loc[rows, "title"] = ch["new_title"]
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
    pred["was_under_why"] = None                      # None = "manager left"
    # 5b. Title changes: the old direct reports (unless they changed title later themselves)
    here = pred.left.astype(str) != "True" if "left" in pred else pd.Series(True, pred.index)
    ctx = {}
    for i, ch in enumerate(changes):
        later = {c["employee_id"] for c in changes[i + 1:]}
        why = {"promotion": "manager promoted", "demotion": "manager demoted",
               "move": "manager moved"}.get(ch["kind"], "manager changed role")
        for eid, conf in ch["reports"].items():
            row = (pred.employee_id == eid) & here
            if eid in later or not row.any():
                continue
            pred.loc[row, ["was_under", "was_under_why"]] = [ch["full_name"], why]
            if ch["vacant"]:                          # stays under the vacant card
                pred.loc[row, ["pred_manager_id", "confidence", "reason"]] = \
                    [f"{ch['employee_id']}-VAC{i + 1}", conf, ""]
                continue
            if ch["employee_id"] in gone_ids:         # the person left later: leaver rules
                continue
            cid = ch["company_id"]
            if cid not in ctx:
                ctx[cid] = in_context(pred[here & (pred.company_id == cid)])
            lvl, func = ctx[cid].loc[eid] if eid in ctx[cid].index else (None, None)
            if func == ch["new_function"] and pd.notna(lvl) and ch["new_level"] is not None \
                    and ch["new_level"] < lvl:        # still fits: stays under the person
                pred.loc[row, ["pred_manager_id", "confidence", "reason"]] = \
                    [ch["employee_id"], conf, ""]
            # otherwise: the rules' new answer stands (and interim below, if needed)
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
    # 8. Vacant cards for title changes: the old title at the old place (never scored)
    vacant = []
    for i, ch in enumerate(changes):
        src = pred[pred.employee_id == ch["employee_id"]]
        if not ch["vacant"] or src.empty:
            continue
        r = src.iloc[0].copy()
        r[:] = None
        for c in ("company_id", "company_name", "industry", "department"):
            r[c] = src.iloc[0][c]
        r["employee_id"], r["full_name"] = f"{ch['employee_id']}-VAC{i + 1}", "Vacant"
        r["title"], r["level"], r["function"] = (ch["old_title"], ch["old_level"],
                                                 ch["old_function"])
        r["pred_manager_id"], r["confidence"] = ch["old_manager"], ch["old_confidence"]
        r["reason"], r["vacant"], r["interim"] = "vacant position", True, False
        r["vacancy_note"] = f"was {ch['full_name']}, now {ch['new_title']}"
        vacant.append(r)
    if vacant:
        pred = pd.concat([pred, pd.DataFrame(vacant)], ignore_index=True)
    return pred
