"""
refresh.py  -  Phase 2: refresh org charts only for companies that changed.

1. Opens a row in runs (trigger, start time, status 'running').
2. Finds companies with needs_refresh = true that have current employees. Companies that
   only appear in someone's past job history (no current employees) are never refreshed.
3. Loads their current employments from the database and runs the existing inference,
   infer.predict(), unchanged (same rules, same decision tree, same threshold).
4. Per company, in one transaction: the company's old reporting_lines get
   is_current = false, the new ones are inserted with is_current = true and this run_id,
   and the company gets needs_refresh = false. Cleanup: only the lines of the company's
   last 2 refreshes are kept; older non-current lines are deleted (KEEP_REFRESHES).
   Employment history is never deleted.
5. Closes the run: end time, status, companies / people processed, lines written, cost
   (0 for the rules: no Gemini tokens, no PDL credits).
If no company needs a refresh, the run is recorded with 0 companies and nothing else happens.
Prints only counts (never the database address or password).

Logfire (db/telemetry.py, only if LOGFIRE_TOKEN is set): one span per run and one child
span per company, with IDs and counts only (people processed, lines drawn, not placed,
seconds, judge, cost). Prices live in db/costs.py.

Run from the repo root:  python refresh.py
"""
import subprocess
import sys
import time

import pandas as pd

import infer
import judge_gemini
from db import costs, telemetry
from db.connect import get_connection

# PDL department words (job_title_role) -> the department names infer.py knows.
# Without this, infer.py would treat "human_resources" like a UK unit name. Words with no
# match are left blank, so the function comes from the title, as for any blank department.
PDL_DEPARTMENTS = {"engineering": "Engineering", "sales": "Sales",
                   "sales_engineering": "Sales", "marketing": "Marketing",
                   "finance": "Finance", "human_resources": "Human Resources",
                   "operations": "Operations", "product": "Product",
                   "support": "Customer Success"}

# Old reporting lines are history, but they add up (~52 MB per full refresh): per company,
# keep the lines of its last 2 refreshes (the current chart and the one before) only.
KEEP_REFRESHES = 2

CURRENT_EMPLOYEES = """
    select e.company_id, c.name as company_name, c.industry, c.source as company_source,
           e.employment_id, e.person_id as employee_id, p.full_name, e.title,
           e.department, e.location
    from employments e
    join people p    on p.person_id = e.person_id
    join companies c on c.company_id = e.company_id
    where e.is_current and e.company_id = any(%s)
    order by e.company_id, e.employment_id"""


def code_version():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def none_if_blank(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)) or v == "":
        return None
    return v.item() if hasattr(v, "item") else v


def gemini_people(conn):
    """{company_id: {person_id, ...}} whose change (not undone, since the company's last
    refresh) asked for the Gemini judge."""
    out = {}
    for cid, pid in conn.execute(
            "select unnest(l.company_ids), l.person_id from change_log l "
            "where l.judge = 'gemini' and l.undone_at is null and l.person_id is not null "
            "and l.created_at > coalesce((select max(c.last_refreshed_at) from companies c "
            "where c.company_id = any(l.company_ids)), '-infinity')").fetchall():
        out.setdefault(cid, set()).add(pid)
    return out


def place_with_gemini(pred, wanted, judge, threshold):
    """Gemini places the wanted people (from the rules' candidate lists); their rows get
    judge 'gemini'. If Gemini fails, the rules' answer stays. Returns a note or None."""
    pred["judge"] = "rules"
    wanted = set(pred.employee_id) & set(wanted)
    if not wanted or judge is None:
        return None if not wanted else "no Gemini key: rules used"
    try:
        answers = judge_gemini.place(pred, wanted, judge)
    except judge_gemini.JudgeError as e:
        return f"Gemini failed ({e}): rules used"
    for eid, a in answers.items():
        row = pred.employee_id == eid
        pred.loc[row, ["pred_manager_id", "confidence", "reason", "judge"]] = \
            [a["manager_id"], a["confidence"], "Gemini: " + a["reason"], "gemini"]
        pred.loc[row, "manager_id"] = a["manager_id"] if a["confidence"] >= threshold else None
    return None


def refresh_company(conn, run_id, cid, people_df, judge=None, wanted=()):
    """Infer one company and save its lines (one transaction). Returns counts for logging.
    infer.predict() treats every company on its own, so one company at a time gives the
    same answer as all at once. wanted: people the Gemini judge should place (if given)."""
    pred, threshold, _ = infer.predict(people_df.drop(columns="company_source"))
    note = place_with_gemini(pred, wanted, judge, threshold)
    has = pred.pred_manager_id.notna()
    values = [(run_id, cid, r.employee_id, int(r.employment_id),
               none_if_blank(r.pred_manager_id), none_if_blank(r.manager_id),
               round(float(r.confidence), 3) if h else None, none_if_blank(r.reason), r.judge)
              for r, h in zip(pred.itertuples(), has)]
    with conn.transaction():                          # one company: all or nothing
        conn.execute("update reporting_lines set is_current = false "
                     "where company_id = %s and is_current", (cid,))
        with conn.cursor() as cur, cur.copy(              # COPY: fast for big companies
                "copy reporting_lines (run_id, company_id, person_id, employment_id, "
                "proposed_manager_id, manager_id, confidence, reason, judge, is_current) "
                "from stdin") as copy:
            for v in values:
                copy.write_row((*v, True))
        # Cleanup: keep only this company's lines from its last KEEP_REFRESHES refreshes.
        # (Employment history is never deleted.)
        deleted = conn.execute(
            "delete from reporting_lines where company_id = %s and not is_current "
            "and run_id not in (select run_id from reporting_lines where company_id = %s "
            "group by run_id order by run_id desc limit %s)",
            (cid, cid, KEEP_REFRESHES)).rowcount
        conn.execute("update companies set needs_refresh = false, last_refreshed_at = now(), "
                     "last_run_id = %s where company_id = %s", (run_id, cid))
    drawn = int(pred.manager_id.notna().sum())
    top = int((pred.reason == "top of company").sum())
    return {"people": len(people_df), "lines_written": len(values), "lines_drawn": drawn,
            "not_placed": len(pred) - drawn - top, "lines_deleted": deleted,
            "threshold": threshold, "gemini_placed": int((pred.judge == "gemini").sum()),
            "note": note}


def main(trigger="refresh", judge=None):
    """judge: a judge_gemini.GeminiJudge to place the people whose change asked for Gemini
    (their tokens and cost go into the runs row), or None = rules only."""
    lf_on = telemetry.sending()
    with get_connection(autocommit=True) as conn, \
            telemetry.span("refresh run ({trigger})", trigger=trigger) as run_span:
        run_id = conn.execute(
            "insert into runs (trigger, status, code_version) values (%s, 'running', %s) "
            "returning run_id", (trigger, code_version())).fetchone()[0]
        run_span.set_attribute("run_id", run_id)
        t0 = time.time()
        totals = {"people": 0, "lines_written": 0, "lines_drawn": 0, "not_placed": 0,
                  "lines_deleted": 0}
        threshold = None
        try:
            todo = [r[0] for r in conn.execute(
                "select company_id from companies c where needs_refresh and exists ("
                "  select 1 from employments e where e.company_id = c.company_id "
                "  and e.is_current) order by company_id").fetchall()]
            model = getattr(judge, "model", costs.DEFAULT_GEMINI_MODEL)
            calls0 = getattr(judge, "calls", 0)
            notes = []
            if todo:
                cur = conn.execute(CURRENT_EMPLOYEES, (todo,))
                df = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
                pdl = df.company_source == "pdl"
                df.loc[pdl, "department"] = df.loc[pdl, "department"].map(PDL_DEPARTMENTS)
                wanted = gemini_people(conn)              # people whose change asked Gemini
                for cid, people_df in df.groupby("company_id"):
                    # Only IDs and counts go to Logfire: never names or titles
                    use_gemini = bool(wanted.get(cid))
                    with telemetry.span("refresh company {company_id}", company_id=cid,
                                        run_id=run_id,
                                        judge="gemini" if use_gemini else "rules") as span:
                        t1 = time.time()
                        tok0 = (getattr(judge, "input_tokens", 0),
                                getattr(judge, "output_tokens", 0))
                        n = refresh_company(conn, run_id, cid, people_df, judge,
                                            wanted.get(cid, ()))
                        cost = costs.run_cost(getattr(judge, "input_tokens", 0) - tok0[0],
                                              getattr(judge, "output_tokens", 0) - tok0[1],
                                              model=model)        # rules only: $0
                        span.set_attributes({
                            "people_processed": n["people"], "lines_drawn": n["lines_drawn"],
                            "not_placed": n["not_placed"], "lines_deleted": n["lines_deleted"],
                            "gemini_placed": n["gemini_placed"],
                            "seconds": round(time.time() - t1, 3), "cost_usd": cost})
                    threshold = n.pop("threshold")
                    if n["note"]:
                        notes.append(f"{cid}: {n['note']}")
                    for k in totals:
                        totals[k] += n[k]
            # Gemini tokens (if the judge was used) and no PDL credits in a refresh
            gem_in = getattr(judge, "input_tokens", 0)
            gem_out = getattr(judge, "output_tokens", 0)
            gem_calls = getattr(judge, "calls", 0) - calls0
            credits = 0
            cost = costs.run_cost(gem_in, gem_out, credits, model=model)
            conn.execute(
                "update runs set finished_at = now(), status = 'succeeded', "
                "companies_processed = %s, people_processed = %s, lines_written = %s, "
                "threshold = %s, gemini_calls = %s, gemini_input_tokens = %s, "
                "gemini_output_tokens = %s, pdl_credits_used = %s, cost_usd = %s, note = %s "
                "where run_id = %s", (len(todo), totals["people"], totals["lines_written"],
                                      threshold, gem_calls, gem_in, gem_out, credits, cost,
                                      "; ".join(notes)[:2000] or None, run_id))
            run_span.set_attributes({
                "status": "succeeded", "companies_processed": len(todo),
                "people_processed": totals["people"], "lines_drawn": totals["lines_drawn"],
                "not_placed": totals["not_placed"], "lines_deleted": totals["lines_deleted"],
                "seconds": round(time.time() - t0, 3),
                "cost_usd": cost, "gemini_input_tokens": gem_in,
                "gemini_output_tokens": gem_out, "pdl_credits_used": credits})
        except Exception as e:
            conn.execute("update runs set finished_at = now(), status = 'failed', "
                         "error = %s where run_id = %s", (f"{type(e).__name__}: {e}"[:2000],
                                                          run_id))
            run_span.set_attribute("status", "failed")
            raise
    logged = "sent to Logfire" if lf_on else "Logfire off (no LOGFIRE_TOKEN)"
    if todo:
        print(f"Run {run_id}: refreshed {len(todo)} companies, {totals['people']:,} people, "
              f"{totals['lines_written']:,} reporting lines written, "
              f"{totals['lines_deleted']:,} old lines cleaned up "
              f"({time.time() - t0:.1f} s, cost ${cost:.2f}; {logged})")
    else:
        print(f"Run {run_id}: no company needs a refresh; nothing done ({logged})")
    return {"run_id": run_id, "companies": todo, "people": totals["people"],
            "lines_written": totals["lines_written"], "cost_usd": cost, "notes": notes}


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "refresh")
