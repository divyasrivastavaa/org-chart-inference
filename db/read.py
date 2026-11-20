"""
db/read.py  -  Phase 2: everything the app reads, straight from the database.
No CSV files. The app (app.py) caches these with st.cache_data.

The frames have the same columns the app used to build from the CSV files
(employees.csv + predictions.csv + truth.csv), so the chart, tabs and what-if code stay
the same:
  employee_id, full_name, title, department, location, company_id, company_name,
  industry, size, level, function, pred_manager_id, confidence, reason,
  true_manager_id, checkable
level / function are read from the title (infer.parse_title), as predictions.csv had them.

Privacy: people with source 'pdl' are real people. Unless show_pdl is True (the visitor
entered DB_VIEW_PIN) their name is replaced by "PDL person #<id>".
"""
import re

import pandas as pd

import infer
from db import connect

# Dataset keys used by the app -> companies.source in the database
SOURCE_OF = {"synthetic": "benchmark", "real": "asx_nzx", "uk": "uk_organogram", "pdl": "pdl"}
DATASET_OF_SOURCE = {v: k for k, v in SOURCE_OF.items()}
# Same mapping as refresh.py: PDL department words -> the department names infer.py knows
PDL_DEPARTMENTS = {"engineering": "Engineering", "sales": "Sales",
                   "sales_engineering": "Sales", "marketing": "Marketing",
                   "finance": "Finance", "human_resources": "Human Resources",
                   "operations": "Operations", "product": "Product",
                   "support": "Customer Success"}


def query(sql, params=None):
    with connect.get_connection() as conn:
        conn.read_only = True
        cur = conn.execute(sql, params)
        return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def mask(frame, show_pdl, name_col="full_name", id_col="employee_id", source_col="person_source"):
    if not show_pdl and len(frame):
        pdl = frame[source_col] == "pdl"
        frame.loc[pdl, name_col] = "PDL person #" + frame.loc[pdl, id_col].astype(str)
    return frame


# ---------------------------------------------------------------- front page
def companies():
    """Every company with current employees: id, name, industry, dataset key, employees.
    'employees' counts the people on the company's chart (people the inference dropped
    as exact duplicates have no line and are left out, as with the CSVs)."""
    df = query("""
        select c.company_id, c.name as company_name, c.industry, c.source,
               c.size_band as size, count(*) as total, count(rl.line_id) as with_line
        from companies c
        join employments e on e.company_id = c.company_id and e.is_current
        left join reporting_lines rl on rl.person_id = e.person_id
                                    and rl.company_id = c.company_id and rl.is_current
        where c.source = any(%s)
        group by c.company_id""", (list(SOURCE_OF.values()),))
    df["dataset"] = df.source.map(DATASET_OF_SOURCE)
    df["employees"] = df.with_line.where(df.with_line > 0, df.total).astype(int)
    return df.drop(columns=["total", "with_line"])


def company_stats(threshold):
    """Per company, at this threshold: lines drawn, and for people with a known true
    manager (answer_key): how many there are, how many got a line, how many are right."""
    lines = query("""
        select company_id, count(*) filter (where proposed_manager_id is not null
                                            and confidence >= %s) as lines
        from reporting_lines where is_current group by company_id""", (threshold,))
    scored = query("""
        select a.company_id, count(*) as scored,
               count(*) filter (where rl.confidence >= %s
                                and rl.proposed_manager_id is not null) as given,
               count(*) filter (where rl.confidence >= %s
                                and rl.proposed_manager_id = a.true_manager_id) as right
        from answer_key a
        left join reporting_lines rl on rl.person_id = a.person_id
                                    and rl.company_id = a.company_id and rl.is_current
        where a.true_manager_id is not null
        group by a.company_id""", (threshold, threshold))
    return lines.merge(scored, on="company_id", how="outer")


def counts():
    """Numbers for captions and the Docs popup."""
    df = query("""
        select c.source, count(distinct c.company_id) as companies,
               count(e.employment_id) as people
        from companies c join employments e on e.company_id = c.company_id and e.is_current
        group by c.source""")
    out = {DATASET_OF_SOURCE.get(r.source, r.source): {"companies": int(r.companies),
                                                      "people": int(r.people)}
           for r in df.itertuples()}
    out["confirmed_lines"] = int(query(
        "select count(*) as n from answer_key a join companies c using (company_id) "
        "where c.source = 'asx_nzx' and a.true_manager_id is not null").n[0])
    return out


def accuracy(threshold, company_ids=None):
    """Precision / coverage over the answer key (optionally only some companies)."""
    df = query("""
        select count(*) as scored,
               count(*) filter (where rl.confidence >= %s
                                and rl.proposed_manager_id is not null) as given,
               count(*) filter (where rl.confidence >= %s
                                and rl.proposed_manager_id = a.true_manager_id) as right
        from answer_key a
        left join reporting_lines rl on rl.person_id = a.person_id
                                    and rl.company_id = a.company_id and rl.is_current
        where a.true_manager_id is not null and a.company_id = any(%s)""",
               (threshold, threshold, company_ids))
    r = df.iloc[0]
    return {"scored": int(r.scored), "given": int(r.given), "right": int(r.right),
            "coverage": r.given / r.scored if r.scored else None,
            "precision": r.right / r.given if r.given else None}


# ---------------------------------------------------------------- company page
PEOPLE_SQL = """
    select e.company_id, c.name as company_name, c.industry, c.size_band as size,
           c.source as company_source, e.person_id as employee_id, p.full_name,
           p.source as person_source, e.title, e.department, e.location,
           rl.line_id, rl.proposed_manager_id as pred_manager_id, rl.confidence, rl.reason,
           a.true_manager_id
    from employments e
    join people p    on p.person_id = e.person_id
    join companies c on c.company_id = e.company_id
    left join reporting_lines rl on rl.person_id = e.person_id
                                and rl.company_id = e.company_id and rl.is_current
    left join answer_key a on a.person_id = e.person_id and a.company_id = e.company_id
    where e.is_current and e.company_id = any(%s)
    order by e.company_id, e.person_id"""


def _departments(df):
    """PDL department words -> our department names (as refresh.py does)."""
    pdl = df.company_source == "pdl"
    df.loc[pdl, "department"] = df.loc[pdl, "department"].map(PDL_DEPARTMENTS)
    return df


def company(company_id, show_pdl):
    """(chart frame, scored frame, truth frame) for one company.
    chart: everyone on the chart, with the current line from the last refresh.
    scored: people with a known true manager (also those without a line), as evaluate.py.
    truth: employee_id, true_manager_id, checkable (can a drawn line be coloured?)."""
    df = _departments(query(PEOPLE_SQL, ([company_id],)))
    df = mask(df, show_pdl)
    # Can a drawn line be checked? Benchmark: everyone. Real data: only a known manager.
    df["checkable"] = (df.company_source == "benchmark") | df.true_manager_id.notna()
    scored = df[df.true_manager_id.notna()][
        ["company_id", "employee_id", "true_manager_id", "pred_manager_id", "confidence"]]
    if df.line_id.notna().any():               # refreshed: people with a line only
        df = df[df.line_id.notna()]            # (exact duplicates were dropped by the rules)
    df["confidence"] = df.confidence.astype(float).fillna(0.0)
    parsed = [infer.parse_title(t, d) for t, d in zip(df.title, df.department)]
    df["level"] = [p[0] for p in parsed]
    df["function"] = [p[1] for p in parsed]
    truth = df[["employee_id", "true_manager_id", "checkable"]]
    chart = df.drop(columns=["line_id", "company_source"])
    return chart.reset_index(drop=True), scored.reset_index(drop=True), truth


def employees(company_ids, show_pdl):
    """Current employees of some companies, with the columns of employees.csv (for the
    in-memory what-if, which re-runs infer.predict on them)."""
    df = _departments(query(PEOPLE_SQL, (list(company_ids),)))
    df = mask(df, show_pdl)
    df["source"] = df.person_source
    return df[["company_id", "company_name", "industry", "size", "employee_id", "full_name",
               "title", "department", "location", "source"]]


def companies_named(names, source):
    """Company ids whose name (any case) is in names, within one dataset."""
    if not names:
        return []
    return query("select company_id from companies where lower(name) = any(%s) "
                 "and source = %s", ([n.lower() for n in names], source)).company_id.tolist()


def names_in(company_id, show_pdl):
    """Names of the people working there now (PDL names masked without the PIN, so a
    'name already taken' message can't reveal a real person)."""
    df = query("select p.person_id as employee_id, p.full_name, p.source as person_source "
               "from employments e join people p using (person_id) "
               "where e.company_id = %s and e.is_current", (company_id,))
    return set(mask(df, show_pdl).full_name)


def sources():
    """Where each real company / organisation comes from (was data/real/sources.csv)."""
    return query("select dataset, company_id, company_name, url, what_it_shows, "
                 "date_checked, licence from sources order by source_id")


# ---------------------------------------------------------------- Database section
def find_people(text, show_pdl, limit=50):
    """People whose name or id contains text. Without the PIN, PDL people are found by
    id only (searching a real person's name must not reveal that they are here)."""
    like = f"%{text.strip()}%"
    df = query("""
        select p.person_id as employee_id, p.full_name, p.source as person_source
        from people p
        where p.person_id ilike %s
           or (p.full_name ilike %s and (p.source <> 'pdl' or %s))
        order by p.person_id limit %s""", (like, like, show_pdl, limit))
    return mask(df, show_pdl)


def job_history(person_id):
    return query("""
        select e.employment_id, e.company_id, c.name as company, e.title, e.department,
               e.seniority, e.start_date, e.end_date, e.is_current, e.change_type,
               e.previous_employment_id, e.source
        from employments e join companies c using (company_id)
        where e.person_id = %s
        order by e.start_date nulls first, e.employment_id""", (person_id,))


def runs():
    return query("""
        select run_id, trigger, status, started_at, finished_at,
               round(extract(epoch from finished_at - started_at)::numeric, 1) as seconds,
               companies_processed, people_processed, lines_written, threshold,
               gemini_calls, gemini_input_tokens, gemini_output_tokens, pdl_credits_used,
               cost_usd, note, error
        from runs order by run_id desc""")


# ---------------------------------------------------------------- read-only SQL
BLOCKED = re.compile(r"set_config|u&", re.I)   # could switch role / settings: refused


def check_sql(sql):
    """(cleaned SQL, None) or (None, the reason it is refused)."""
    q = sql.strip().rstrip(";").strip()
    if not q:
        return None, "Type a query."
    if ";" in q:
        return None, "One statement only (no ';' in the middle)."
    if not re.match(r"(select|with)\b", q, re.I):
        return None, "Only SELECT or WITH queries are allowed."
    if BLOCKED.search(q):
        return None, "This query uses a function that is not allowed here (set_config)."
    if not re.search(r"\blimit\b", q, re.I):
        q = f"select * from (\n{q}\n) as q limit 500"
    return q, None


def run_readonly(sql, show_pdl):
    """Runs one checked SELECT as role app_readonly in a READ ONLY transaction with a
    5-second timeout, then rolls back. Returns at most 500 rows."""
    q, problem = check_sql(sql)
    if problem:
        raise ValueError(problem)
    with connect.get_connection() as conn:
        try:
            with conn.transaction():
                conn.execute("set transaction read only")
                conn.execute("set local statement_timeout = '5s'")
                conn.execute("set local role app_readonly")
                if show_pdl:
                    conn.execute("set local app.show_pdl_names = 'on'")
                cur = conn.execute(q)
                rows = cur.fetchmany(500)
                frame = pd.DataFrame(rows, columns=[d.name for d in cur.description])
                raise _Done(frame)                  # leave the block = roll back
        except _Done as done:
            return done.frame


class _Done(Exception):
    def __init__(self, frame):
        self.frame = frame


EXAMPLES = {
    "One person's job history": """select p.full_name, c.name as company, e.title, e.start_date,
       e.end_date, e.is_current, e.change_type
from employments e
join people p using (person_id)
join companies c using (company_id)
where e.person_id = 'C001-E0002'
order by e.start_date nulls first, e.employment_id""",
    "A company's current org chart": """select p.full_name, e.title, m.full_name as manager, rl.confidence, rl.reason
from reporting_lines rl
join people p      on p.person_id = rl.person_id
left join people m on m.person_id = rl.manager_id
left join employments e on e.person_id = rl.person_id
                       and e.company_id = rl.company_id and e.is_current
where rl.company_id = 'C001' and rl.is_current
order by m.full_name nulls first, p.full_name""",
    "Companies that need a refresh": """select c.company_id, c.name, c.source, c.last_refreshed_at
from companies c
where c.needs_refresh
  and exists (select 1 from employments e where e.company_id = c.company_id and e.is_current)
order by c.name""",
    "Cost per run": """select run_id, trigger, status, started_at,
       finished_at - started_at as duration, companies_processed, people_processed,
       gemini_input_tokens + gemini_output_tokens as gemini_tokens, pdl_credits_used, cost_usd
from runs
order by run_id desc""",
    "People without a manager (one company)": """select p.full_name, e.title, rl.reason
from reporting_lines rl
join people p on p.person_id = rl.person_id
left join employments e on e.person_id = rl.person_id
                       and e.company_id = rl.company_id and e.is_current
where rl.company_id = 'C001' and rl.is_current and rl.manager_id is null
  and rl.reason is distinct from 'top of company'
order by rl.reason, p.full_name""",
    "Cost and credits per day and trigger": """select started_at::date as day, trigger, count(*) as runs,
       sum(people_processed) as people, sum(pdl_credits_used) as pdl_credits,
       sum(gemini_input_tokens + gemini_output_tokens) as gemini_tokens,
       sum(cost_usd) as cost_usd
from runs
group by 1, 2
order by 1 desc, 2""",
    "Precision and coverage per dataset": """select c.source, count(*) as people_scored,
       count(rl.manager_id) as lines_drawn,
       round(100.0 * count(*) filter (where rl.manager_id = a.true_manager_id)
             / nullif(count(rl.manager_id), 0), 1) as precision_pct,
       round(100.0 * count(rl.manager_id) / count(*), 1) as coverage_pct
from answer_key a
join companies c using (company_id)
left join reporting_lines rl on rl.person_id = a.person_id
                            and rl.company_id = a.company_id and rl.is_current
where a.true_manager_id is not null
group by c.source
order by c.source""",
    "Job changes and title changes (job history)": """select c.source as company_source, e.change_type, count(*) as jobs
from employments e
join companies c using (company_id)
group by 1, 2
order by 1, 2""",
}
