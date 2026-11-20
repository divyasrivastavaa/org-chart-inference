"""
scripts/test_app_changes.py  -  Phase 2, Step 10: changes made in the app are saved in the
database. Drives the real app.py (Streamlit AppTest, no browser) with the rules only:
no PDL and no Gemini call.

 0. without the edit PIN: Add / Remove / Promote / Refresh are disabled
 1. add a person to C001                      -> reload: they are there, waiting
 2. change a title in C001                    -> reload: new title shown
 3. move C001's Chief Data Officer to C002    -> reload: gone from C001, in C002
 4. Refresh changed companies                 -> only C001 and C002; runs row logged
 5. undo all three (reload between)           -> reload: as before
 6. refresh again                             -> only C001 and C002; same lines as at start
 7. save a query in the SQL tab               -> reload: in the dropdown
 8. put the database back exactly as it was (lines, jobs, people, change log, runs, saved
    query), then check the totals.
Every "reload" is a brand-new AppTest session, so nothing can come from browser memory.
The edit PIN is read from the secrets and typed into the app's PIN box; never printed.
Run from the repo root:  python scripts/test_app_changes.py
"""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db.connect import get_connection, get_secret  # noqa: E402

A, B = "C001", "C002"
NEW_TITLE = "Engineering Director, Platform"
failures = []


def check(ok, label):
    print(("  ok    " if ok else "  FAIL  ") + label)
    if not ok:
        failures.append(label)


def q(sql, *p):
    with get_connection() as c:
        c.read_only = True
        return c.execute(sql, p or None).fetchall()


def page(company=None, edit=True):
    """A fresh app session (= a page reload), with the edit PIN typed in if edit."""
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    if company:
        at.session_state["company_select"] = company
    at.run()
    if edit:
        box = next(t for t in at.text_input if t.label.startswith("Edit PIN"))
        box.set_value(get_secret("DB_EDIT_PIN")).run()
    if at.exception:
        print("        exception:", at.exception[0].message[:300])
    return at


def button(at, label):
    return next(b for b in at.button if b.label == label)


def people(at):
    return next(d.value for d in at.dataframe
                if "Name" in d.value.columns and "Status" in d.value.columns)


def flashes(at):
    return [m.value for m in list(at.success) + list(at.info) + list(at.warning)
            + list(at.error)]


# ---------------------------------------------------------------- 0. snapshot
waiting = q("select count(*) from companies c where needs_refresh and exists (select 1 from "
            "employments e where e.company_id = c.company_id and e.is_current)")[0][0]
if waiting or q("select count(*) from change_log where undone_at is null")[0][0]:
    sys.exit("Stopped: there are saved changes or companies waiting; the test needs a clean start.")
snap = {
    "max_emp": q("select coalesce(max(employment_id), 0) from employments")[0][0],
    "max_change": q("select coalesce(max(change_id), 0) from change_log")[0][0],
    "max_run": q("select coalesce(max(run_id), 0) from runs")[0][0],
    "people": {r[0] for r in q("select person_id from people where source = 'manual'")},
    "queries": {r[0] for r in q("select query_id from saved_queries")},
    "lines": q("select line_id, run_id, company_id, person_id, employment_id, "
               "proposed_manager_id, manager_id, confidence, reason, judge, is_current, "
               "created_at from reporting_lines where company_id = any(%s)", [A, B]),
    "companies": q("select company_id, needs_refresh, last_refreshed_at, last_run_id "
                   "from companies where company_id = any(%s)", [A, B]),
    "totals": q("select (select count(*) from companies), (select count(*) from people), "
                "(select count(*) from employments), (select count(*) from reporting_lines), "
                "(select count(*) from runs)")[0],
}
current_before = {(r[3], r[5], float(r[7]) if r[7] is not None else None)
                  for r in snap["lines"] if r[10]}
print(f"Start: totals (companies, people, employments, lines, runs) = {snap['totals']}")

try:
    # ------------------------------------------------------------ 0. no PIN: read only
    print("0) without the edit PIN")
    at = page(A, edit=False)
    check(all(button(at, b).disabled for b in ("Add people", "Remove people",
                                               "Promote / change title",
                                               "Refresh changed companies")),
          "Add / Remove / Promote / Refresh disabled")
    check(len(people(at)) > 0, "viewing still works")
    box = next(t for t in at.text_input if t.label.startswith("Edit PIN"))
    box.set_value("not-the-pin").run()
    check(any("Wrong edit PIN" in c.value for c in at.sidebar.caption), "wrong PIN refused")

    # ------------------------------------------------------------ 1. add a person
    print("1) add a person")
    at = page(A)
    button(at, "Add people").click().run()
    next(t for t in at.text_area if t.label.startswith("One person per line")) \
        .set_value("Test Hire | Software Engineer").run()
    button(at, "Add and save them").click().run()
    check(not at.exception, "saved without error: " + " | ".join(flashes(at))[:120])
    at = page(A, edit=False)                                     # reload
    row = people(at)[lambda t: t.Name == "Test Hire"]
    check(len(row) == 1 and "waiting for refresh" in row.Status.iloc[0],
          f"after reload: Test Hire is on {A}, waiting for a refresh")

    # ------------------------------------------------------------ 2. change a title
    print("2) change a title")
    at = page(A)
    button(at, "Promote / change title").click().run()
    next(s for s in at.selectbox if s.label == "Who?").set_value("C001-E0003").run()
    next(t for t in at.text_input if t.label == "New job title").set_value(NEW_TITLE).run()
    button(at, "Apply change").click().run()
    check(not at.exception, "saved without error: " + " | ".join(flashes(at))[:120])
    at = page(A, edit=False)                                     # reload
    check(NEW_TITLE in set(people(at).Title), f"after reload: title is '{NEW_TITLE}'")

    # ------------------------------------------------------------ 3. move to C002
    print("3) move someone to another company")
    mover = q("select person_id from employments where company_id = %s and is_current "
              "and title = 'Chief Data Officer'", A)[0][0]
    at = page(A)
    button(at, "Remove people").click().run()
    next(m for m in at.multiselect if m.label == "Who is leaving?").set_value([mover]).run()
    next(t for t in at.text_input if t.label == "Moving to (optional)").set_value(B).run()
    button(at, "Remove them").click().run()
    check(not at.exception, "saved without error: " + " | ".join(flashes(at))[:120])
    at = page(A, edit=False)                                     # reload
    check("Chief Data Officer" not in set(people(at).Title), f"after reload: gone from {A}")
    at = page(B, edit=False)
    check("Chief Data Officer" in set(people(at).Title), f"after reload: on {B}")

    # ------------------------------------------------------------ 4. refresh
    print("4) refresh changed companies")
    at = page()
    button(at, "Refresh changed companies").click().run()
    msg = " | ".join(flashes(at))
    check("Refreshed 2 companies" in msg and A in msg and B in msg, msg[:150])
    run = q("select run_id, trigger, status, companies_processed, people_processed, "
            "gemini_calls, cost_usd from runs order by run_id desc limit 1")[0]
    check(run[1:4] == ("app_change", "succeeded", 2) and run[5] == 0,
          f"runs row: {run[:6]}, cost ${run[6]}")
    at = page(A, edit=False)
    row = people(at)[lambda t: t.Name == "Test Hire"]
    check(len(row) == 1 and "waiting" not in row.Status.iloc[0],
          f"Test Hire placed by the rules: {row.Status.iloc[0] if len(row) else '-'}")
    bvn = [d.value for d in at.dataframe if "Change" in d.value.columns]
    check(bool(bvn), f"Before vs Now (two refreshes) shows {len(bvn[0]) if bvn else 0} change(s)")

    # ------------------------------------------------------------ 5. undo all three
    print("5) undo all three")
    for _ in range(3):
        at = page(A)
        button(at, "Undo last change").click().run()
        check(any(f.startswith("Undone:") for f in flashes(at)),
              " | ".join(flashes(at))[:120])
    at = page(A, edit=False)                                     # reload
    t = people(at)
    check("Test Hire" not in set(t.Name) and NEW_TITLE not in set(t.Title)
          and "Chief Data Officer" in set(t.Title), f"after reload: {A} is as before")

    # ------------------------------------------------------------ 6. refresh again
    print("6) refresh again")
    at = page()
    button(at, "Refresh changed companies").click().run()
    msg = " | ".join(flashes(at))
    check("Refreshed 2 companies" in msg, msg[:150])
    now = {(r[0], r[1], float(r[2]) if r[2] is not None else None) for r in q(
        "select person_id, proposed_manager_id, confidence from reporting_lines "
        "where company_id = any(%s) and is_current", [A, B])}
    check(now == current_before, f"current lines of {A}, {B} are the same as at the start")

    # ------------------------------------------------------------ 7. save a query
    print("7) save a query")
    at = page()
    at.session_state["db_view"] = True
    at.run()
    next(t for t in at.text_input if t.label.startswith("Name to save")) \
        .set_value("Test saved query").run()
    button(at, "Save query").click().run()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)    # reload
    at.session_state["db_view"] = True
    at.run()
    box = next(s for s in at.selectbox if s.label == "Example queries")
    check("Saved: Test saved query" in box.options, "after reload: in the dropdown")

finally:
    # ------------------------------------------------------------ 8. back to the start
    print("8) put the database back as it was")
    keep_ids = {r[0] for r in snap["lines"]}
    with get_connection() as c:                     # one transaction
        ex = lambda s, *p: c.execute(s, p or None).rowcount          # noqa: E731
        n = {
            "test lines deleted": ex("delete from reporting_lines where run_id > %s",
                                     snap["max_run"]),
        }
        have = {r[0] for r in c.execute("select line_id from reporting_lines "
                                        "where company_id = any(%s)", ([A, B],)).fetchall()}
        back = [r for r in snap["lines"] if r[0] not in have]
        cols = ("line_id, run_id, company_id, person_id, employment_id, proposed_manager_id, "
                "manager_id, confidence, reason, judge, is_current, created_at")
        with c.cursor() as cur:
            cur.executemany(f"insert into reporting_lines ({cols}) overriding system value "
                            "values (" + ", ".join(["%s"] * 12) + ")", back)
        n["original lines put back"] = len(back)
        n["original lines current again"] = ex(
            "update reporting_lines set is_current = true where line_id = any(%s)",
            [r[0] for r in snap["lines"] if r[10]])
        n["test jobs deleted"] = ex("delete from employments where employment_id > %s",
                                    snap["max_emp"])
        n["original jobs current again"] = ex(
            "update employments set is_current = true, end_date = null "
            "where employment_id in (select employment_id from reporting_lines "
            "where line_id = any(%s)) and not is_current",
            [r[0] for r in snap["lines"] if r[10]])
        n["test people deleted"] = ex(
            "delete from people where source = 'manual' and not person_id = any(%s)",
            list(snap["people"]))
        n["change log rows deleted"] = ex("delete from change_log where change_id > %s",
                                          snap["max_change"])
        for cid, needs, refreshed, last_run in snap["companies"]:
            ex("update companies set needs_refresh = %s, last_refreshed_at = %s, "
               "last_run_id = %s where company_id = %s", needs, refreshed, last_run, cid)
        n["test runs deleted"] = ex("delete from runs where run_id > %s", snap["max_run"])
        n["test saved queries deleted"] = ex(
            "delete from saved_queries where not query_id = any(%s)", list(snap["queries"]))
    for k, v in n.items():
        print(f"  {k:<32} {v}")
    totals = q("select (select count(*) from companies), (select count(*) from people), "
               "(select count(*) from employments), (select count(*) from reporting_lines), "
               "(select count(*) from runs)")[0]
    lines_now = q("select line_id, run_id, is_current from reporting_lines "
                  "where company_id = any(%s) order by line_id", [A, B])
    check(totals == snap["totals"], f"totals back to the start: {totals}")
    check(lines_now == sorted((r[0], r[1], r[10]) for r in snap["lines"]),
          f"{A}, {B}: reporting lines identical to the start")
    check(q("select count(*) from companies c where needs_refresh and exists (select 1 from "
            "employments e where e.company_id = c.company_id and e.is_current)")[0][0] == 0,
          "nothing waiting for a refresh")

print(f"\n{'ALL PASSED' if not failures else str(len(failures)) + ' FAILED'}")
sys.exit(1 if failures else 0)
