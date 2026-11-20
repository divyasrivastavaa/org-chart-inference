"""
refresh.py  -  Phase 2: refresh org charts only for companies that changed.

1. Opens a row in runs (trigger, start time, status 'running').
2. Finds companies with needs_refresh = true that have current employees. Companies that
   only appear in someone's past job history (no current employees) are never refreshed.
3. Loads their current employments from the database and runs the existing inference,
   infer.predict(), unchanged (same rules, same decision tree, same threshold).
4. Per company, in one transaction: the company's old reporting_lines get
   is_current = false, the new ones are inserted with is_current = true and this run_id,
   and the company gets needs_refresh = false.
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


def refresh_company(conn, run_id, cid, people_df):
    """Infer one company and save its lines (one transaction). Returns counts for logging.
    infer.predict() treats every company on its own, so one company at a time gives the
    same answer as all at once."""
    pred, threshold, _ = infer.predict(people_df.drop(columns="company_source"))
    has = pred.pred_manager_id.notna()
    values = [(run_id, cid, r.employee_id, int(r.employment_id),
               none_if_blank(r.pred_manager_id), none_if_blank(r.manager_id),
               round(float(r.confidence), 3) if h else None, none_if_blank(r.reason))
              for r, h in zip(pred.itertuples(), has)]
    with conn.transaction():                          # one company: all or nothing
        conn.execute("update reporting_lines set is_current = false "
                     "where company_id = %s and is_current", (cid,))
        conn.cursor().executemany(
            "insert into reporting_lines (run_id, company_id, person_id, employment_id, "
            "proposed_manager_id, manager_id, confidence, reason, judge, is_current) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, 'rules', true)", values)
        conn.execute("update companies set needs_refresh = false, last_refreshed_at = now(), "
                     "last_run_id = %s where company_id = %s", (run_id, cid))
    drawn = int(pred.manager_id.notna().sum())
    top = int((pred.reason == "top of company").sum())
    return {"people": len(people_df), "lines_written": len(values), "lines_drawn": drawn,
            "not_placed": len(pred) - drawn - top, "threshold": threshold}


def main(trigger="refresh"):
    lf_on = telemetry.sending()
    with get_connection(autocommit=True) as conn, \
            telemetry.span("refresh run ({trigger})", trigger=trigger) as run_span:
        run_id = conn.execute(
            "insert into runs (trigger, status, code_version) values (%s, 'running', %s) "
            "returning run_id", (trigger, code_version())).fetchone()[0]
        run_span.set_attribute("run_id", run_id)
        t0 = time.time()
        totals = {"people": 0, "lines_written": 0, "lines_drawn": 0, "not_placed": 0}
        threshold = None
        try:
            todo = [r[0] for r in conn.execute(
                "select company_id from companies c where needs_refresh and exists ("
                "  select 1 from employments e where e.company_id = c.company_id "
                "  and e.is_current) order by company_id").fetchall()]
            if todo:
                cur = conn.execute(CURRENT_EMPLOYEES, (todo,))
                df = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
                pdl = df.company_source == "pdl"
                df.loc[pdl, "department"] = df.loc[pdl, "department"].map(PDL_DEPARTMENTS)
                for cid, people_df in df.groupby("company_id"):
                    # Only IDs and counts go to Logfire: never names or titles
                    with telemetry.span("refresh company {company_id}", company_id=cid,
                                        run_id=run_id, judge="rules") as span:
                        t1 = time.time()
                        n = refresh_company(conn, run_id, cid, people_df)
                        cost = costs.run_cost()                  # rules: no tokens, $0
                        span.set_attributes({
                            "people_processed": n["people"], "lines_drawn": n["lines_drawn"],
                            "not_placed": n["not_placed"],
                            "seconds": round(time.time() - t1, 3), "cost_usd": cost})
                    threshold = n.pop("threshold")
                    for k in totals:
                        totals[k] += n[k]
            # Rules only: no Gemini tokens and no PDL credits in a refresh (yet)
            gem_in = gem_out = credits = 0
            cost = costs.run_cost(gem_in, gem_out, credits)
            conn.execute(
                "update runs set finished_at = now(), status = 'succeeded', "
                "companies_processed = %s, people_processed = %s, lines_written = %s, "
                "threshold = %s, gemini_calls = 0, gemini_input_tokens = %s, "
                "gemini_output_tokens = %s, pdl_credits_used = %s, cost_usd = %s "
                "where run_id = %s", (len(todo), totals["people"], totals["lines_written"],
                                      threshold, gem_in, gem_out, credits, cost, run_id))
            run_span.set_attributes({
                "status": "succeeded", "companies_processed": len(todo),
                "people_processed": totals["people"], "lines_drawn": totals["lines_drawn"],
                "not_placed": totals["not_placed"], "seconds": round(time.time() - t0, 3),
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
              f"{totals['lines_written']:,} reporting lines written "
              f"({time.time() - t0:.1f} s, cost ${cost:.2f}; {logged})")
    else:
        print(f"Run {run_id}: no company needs a refresh; nothing done ({logged})")
    return run_id


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "refresh")
