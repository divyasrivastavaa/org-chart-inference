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

Run from the repo root:  python refresh.py
"""
import subprocess
import sys
import time

import pandas as pd

import infer
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


def main(trigger="refresh"):
    with get_connection(autocommit=True) as conn:
        run_id = conn.execute(
            "insert into runs (trigger, status, code_version) values (%s, 'running', %s) "
            "returning run_id", (trigger, code_version())).fetchone()[0]
        t0 = time.time()
        try:
            todo = [r[0] for r in conn.execute(
                "select company_id from companies c where needs_refresh and exists ("
                "  select 1 from employments e where e.company_id = c.company_id "
                "  and e.is_current) order by company_id").fetchall()]
            people = lines = 0
            threshold = None
            if todo:
                cur = conn.execute(CURRENT_EMPLOYEES, (todo,))
                df = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
                pdl = df.company_source == "pdl"
                df.loc[pdl, "department"] = df.loc[pdl, "department"].map(PDL_DEPARTMENTS)
                pred, threshold, _ = infer.predict(df.drop(columns="company_source"))
                for cid, rows in pred.groupby("company_id"):
                    has = rows.pred_manager_id.notna()
                    values = [
                        (run_id, cid, r.employee_id, int(r.employment_id),
                         none_if_blank(r.pred_manager_id), none_if_blank(r.manager_id),
                         round(float(r.confidence), 3) if h else None,
                         none_if_blank(r.reason))
                        for r, h in zip(rows.itertuples(), has)]
                    with conn.transaction():          # one company: all or nothing
                        conn.execute("update reporting_lines set is_current = false "
                                     "where company_id = %s and is_current", (cid,))
                        conn.cursor().executemany(
                            "insert into reporting_lines (run_id, company_id, person_id, "
                            "employment_id, proposed_manager_id, manager_id, confidence, "
                            "reason, judge, is_current) "
                            "values (%s, %s, %s, %s, %s, %s, %s, %s, 'rules', true)", values)
                        conn.execute("update companies set needs_refresh = false, "
                                     "last_refreshed_at = now(), last_run_id = %s "
                                     "where company_id = %s", (run_id, cid))
                    people += len(df[df.company_id == cid])
                    lines += len(values)
            conn.execute(
                "update runs set finished_at = now(), status = 'succeeded', "
                "companies_processed = %s, people_processed = %s, lines_written = %s, "
                "threshold = %s, gemini_calls = 0, gemini_input_tokens = 0, "
                "gemini_output_tokens = 0, pdl_credits_used = 0, cost_usd = 0 "
                "where run_id = %s", (len(todo), people, lines, threshold, run_id))
        except Exception as e:
            conn.execute("update runs set finished_at = now(), status = 'failed', "
                         "error = %s where run_id = %s", (f"{type(e).__name__}: {e}"[:2000],
                                                          run_id))
            raise
    if todo:
        print(f"Run {run_id}: refreshed {len(todo)} companies, {people:,} people, "
              f"{lines:,} reporting lines written ({time.time() - t0:.1f} s, cost $0)")
    else:
        print(f"Run {run_id}: no company needs a refresh; nothing done")
    return run_id


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "refresh")
