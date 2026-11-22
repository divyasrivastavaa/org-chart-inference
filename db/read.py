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

PDL text is stored in lowercase, as PDL sent it; db/display.py puts it in proper case here,
when it is read for the page.
Privacy: people with source 'pdl' are real people. The app shows their name and current job
only: job_history() and the SQL tab's role (migration 008) never return their past jobs.
"""
import re
import threading

import pandas as pd
import psycopg

import infer
from db import connect, display

# Dataset keys used by the app -> companies.source in the database
SOURCE_OF = {"companies": "pdl_company", "pdl": "pdl", "manual": "manual"}
DATASET_OF_SOURCE = {v: k for k, v in SOURCE_OF.items()}
# Same mapping as refresh.py: PDL department words -> the department names infer.py knows
PDL_DEPARTMENTS = {"engineering": "Engineering", "sales": "Sales",
                   "sales_engineering": "Sales", "marketing": "Marketing",
                   "finance": "Finance", "human_resources": "Human Resources",
                   "operations": "Operations", "product": "Product",
                   "support": "Customer Success"}


# One read-only connection per server, reused: opening a new one costs several trips to the
# database, which is the slowest part of a page when the app and database are far apart.
_lock = threading.Lock()
_conn = None
_made_by = None


def _drop_reader():
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except psycopg.Error:
            pass
    _conn = None


def _reader():
    global _conn, _made_by
    if _conn is None or _conn.closed or _made_by is not connect.get_connection:
        _drop_reader()
        _conn = connect.get_connection(autocommit=True, prepare_threshold=None)
        _conn.execute("set default_transaction_read_only = on")
        _made_by = connect.get_connection
    return _conn


def read_many(statements):
    """Runs [(sql, params), ...] and returns one frame each. They are sent together (one
    trip to the database). If the kept connection was dropped, reconnects and tries once more."""
    with _lock:
        for attempt in (1, 2):
            try:
                conn = _reader()
                if psycopg.Pipeline.is_supported():
                    with conn.pipeline():
                        cursors = [conn.execute(q, p) for q, p in statements]
                else:
                    cursors = [conn.execute(q, p) for q, p in statements]
                return [pd.DataFrame(c.fetchall(), columns=[d.name for d in c.description])
                        for c in cursors]
            except psycopg.Error as e:
                _drop_reader()
                if attempt == 2 or not isinstance(e, psycopg.OperationalError):
                    raise


def query(sql, params=None):
    return read_many([(sql, params)])[0]


def page_state(company_id):
    """One small query per page: what changed? The versions are the cache keys, so cached
    data is reused until that company (or, for the front page, any company) changes.
    A saved change counts too: it changes the page before the next refresh."""
    r = query("""
        select (select coalesce(max(last_run_id), 0) from companies) as run_id,
               (select coalesce(max(change_id), 0) from change_log) as change_id,
               (select count(undone_at) from change_log) as undone,
               (select count(*) from companies c where c.needs_refresh and exists
                   (select 1 from employments e where e.company_id = c.company_id
                    and e.is_current)) as waiting,
               l.change_id as last_id, l.summary as last_summary,
               (select last_run_id from companies where company_id = %(c)s) as company_run,
               (select needs_refresh from companies where company_id = %(c)s) as needs,
               (select coalesce(max(change_id), 0) from change_log
                where %(c)s = any(company_ids)) as company_change,
               (select count(undone_at) from change_log
                where %(c)s = any(company_ids)) as company_undone
        from (select 1) as one
        left join lateral (select change_id, summary from change_log where undone_at is null
                           order by change_id desc limit 1) l on true""",
              {"c": company_id}).iloc[0]

    def number(x):
        return None if pd.isna(x) else int(x)

    return {"company_id": company_id, "waiting": int(r.waiting),
            "front": (int(r.run_id), int(r.change_id), int(r.undone), int(r.waiting)),
            "company": (number(r.company_run), None if pd.isna(r.needs) else bool(r.needs),
                        int(r.company_change), int(r.company_undone)),
            "last": {"summary": r.last_summary} if pd.notna(r.last_id) else None}


# ---------------------------------------------------------------- front page
def companies():
    """Every company with current employees: id, name, industry, dataset key, employees.
    'employees' counts the people on the company's chart (people the inference dropped
    as exact duplicates have no line and are left out, as with the CSVs)."""
    df = query("""
        select c.company_id, c.name as company_name, c.industry, c.source, c.source_ref,
               c.size_band as size, count(*) as total, count(rl.line_id) as with_line
        from companies c
        join employments e on e.company_id = c.company_id and e.is_current
        left join reporting_lines rl on rl.person_id = e.person_id
                                    and rl.company_id = c.company_id and rl.is_current
        where c.source = any(%s)
        group by c.company_id""", (list(SOURCE_OF.values()),))
    pdl = df.source == "pdl"
    df["company_name"] = display.apply(df.company_name, pdl, display.proper_title)
    df["industry"] = display.apply(df.industry, pdl, display.proper_title)
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
    return {DATASET_OF_SOURCE.get(r.source, r.source): {"companies": int(r.companies),
                                                       "people": int(r.people)}
            for r in df.itertuples()}


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
           rl.judge, a.true_manager_id,
           -- saved after the company's last refresh, or the company has saved changes
           -- (e.g. an undo reopened an old job): not placed yet
           (c.last_refreshed_at is null or e.recorded_at > c.last_refreshed_at
            or c.needs_refresh) as pending
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


def company(company_id):
    """(chart frame, scored frame, truth frame) for one company: one query per table, sent
    together, only the columns the page uses.
    chart: everyone on the chart, with the current line from the last refresh.
    scored: people with a known true manager (also those without a line), as evaluate.py.
    truth: employee_id, true_manager_id, checkable (can a drawn line be coloured?)."""
    info, jobs, who, lines, key = read_many([
        ("select name as company_name, industry, size_band as size, source as company_source, "
         "last_refreshed_at, needs_refresh from companies where company_id = %s", (company_id,)),
        ("select person_id as employee_id, title, department, location, recorded_at "
         "from employments where company_id = %s and is_current order by person_id",
         (company_id,)),
        ("select p.person_id as employee_id, p.full_name, p.source as person_source "
         "from employments e join people p using (person_id) "
         "where e.company_id = %s and e.is_current", (company_id,)),
        ("select person_id as employee_id, line_id, proposed_manager_id as pred_manager_id, "
         "confidence, reason, judge from reporting_lines "
         "where company_id = %s and is_current", (company_id,)),
        ("select person_id as employee_id, true_manager_id from answer_key "
         "where company_id = %s", (company_id,))])
    co = info.iloc[0]
    df = jobs.merge(who, on="employee_id", how="left").merge(lines, on="employee_id", how="left") \
        .merge(key, on="employee_id", how="left")
    for col in ("pred_manager_id", "reason", "judge", "true_manager_id"):
        df[col] = df[col].astype(object).where(df[col].notna(), None)
    for col in ("company_name", "industry", "size", "company_source"):
        df[col] = co[col]
    df["company_id"] = company_id
    # Saved after the company's last refresh, or the company has saved changes (e.g. an undo
    # reopened an old job): not placed yet
    df["pending"] = True if pd.isna(co.last_refreshed_at) or co.needs_refresh \
        else df.recorded_at > co.last_refreshed_at
    df = _departments(df.drop(columns="recorded_at"))
    # Can a drawn line be checked? PDL companies: everyone. Other data: only a known manager.
    df["checkable"] = (df.company_source == "pdl_company") | df.true_manager_id.notna()
    scored = df[df.true_manager_id.notna()][
        ["company_id", "employee_id", "true_manager_id", "pred_manager_id", "confidence"]]
    # Refreshed: people with a line, plus people saved since the last refresh (waiting).
    # (People without a line from before are exact duplicates the rules dropped.)
    if df.line_id.notna().any():
        df = df[df.line_id.notna() | df.pending.astype(bool)].copy()
    waiting = df.line_id.isna()
    df.loc[waiting, "reason"] = "waiting for refresh (saved change)"
    df["judge"] = df.judge.fillna("rules")
    df["confidence"] = df.confidence.astype(float).fillna(0.0)
    parsed = [infer.parse_title(t, d) for t, d in zip(df.title, df.department)]
    df["level"] = [p[0] for p in parsed]
    df["function"] = [p[1] for p in parsed]
    scored["confidence"] = scored.confidence.astype(float)
    truth = df[["employee_id", "true_manager_id", "checkable"]]
    # Shown in proper case (level and function above were read from the original text)
    who_pdl, co_pdl = df.person_source == "pdl", df.company_source == "pdl"
    df = df.assign(full_name=display.apply(df.full_name, who_pdl, display.proper_name),
                   title=display.apply(df.title, who_pdl, display.proper_title),
                   company_name=display.apply(df.company_name, co_pdl, display.proper_title),
                   industry=display.apply(df.industry, co_pdl, display.proper_title))
    chart = df.drop(columns=["line_id", "company_source"])
    return chart.reset_index(drop=True), scored.reset_index(drop=True), truth


def pending_changes(company_id):
    """Saved changes for this company that the last refresh hasn't used yet."""
    return query("""
        select l.change_id, l.created_at, l.kind, l.summary, l.judge
        from change_log l join companies c on c.company_id = %s
        where %s = any(l.company_ids) and l.undone_at is null
          and (c.last_refreshed_at is null or l.created_at > c.last_refreshed_at)
        order by l.change_id""", (company_id, company_id))


def last_two_refreshes(company_id):
    """The company's lines from its previous and its latest refresh (the cleanup rule keeps
    exactly these two), with the answer key. Columns: run_id, employee_id, pred_manager_id,
    confidence, reason, true_manager_id, started_at."""
    df = query("""
        with r as (select run_id from reporting_lines where company_id = %s
                   group by run_id order by run_id desc limit 2)
        select rl.run_id, ru.started_at, rl.person_id as employee_id,
               rl.proposed_manager_id as pred_manager_id, rl.confidence, rl.reason,
               a.true_manager_id
        from reporting_lines rl
        join r using (run_id)
        join runs ru using (run_id)
        left join answer_key a on a.person_id = rl.person_id and a.company_id = rl.company_id
        where rl.company_id = %s""", (company_id, company_id))
    df["confidence"] = df.confidence.astype(float)
    return df


def database_bytes():
    """The database's current size, in bytes."""
    return int(query("select pg_database_size(current_database()) as n").n[0])


def waiting_companies():
    """How many companies have saved changes waiting for a refresh."""
    return int(query("select count(*) as n from companies c where needs_refresh and exists "
                     "(select 1 from employments e where e.company_id = c.company_id "
                     "and e.is_current)").n[0])


def saved_queries():
    return query("select name, sql from saved_queries order by name")


def employees(company_ids):
    """Current employees of some companies, with the columns of employees.csv (for the
    in-memory what-if, which re-runs infer.predict on them)."""
    df = _departments(query(PEOPLE_SQL, (list(company_ids),)))
    df["source"] = df.person_source
    return df[["company_id", "company_name", "industry", "size", "employee_id", "full_name",
               "title", "department", "location", "source"]]


def companies_named(names, source):
    """Company ids whose name (any case) is in names, within one dataset."""
    if not names:
        return []
    return query("select company_id from companies where lower(name) = any(%s) "
                 "and source = %s", ([n.lower() for n in names], source)).company_id.tolist()


def names_in(company_id):
    """Names of the people working there now."""
    df = query("select p.full_name, p.source from employments e join people p using (person_id) "
               "where e.company_id = %s and e.is_current", (company_id,))
    return set(display.apply(df.full_name, df.source == "pdl", display.proper_name))


# ---------------------------------------------------------------- Database section
def find_people(text, limit=50):
    """People whose name or id contains text."""
    like = f"%{text.strip()}%"
    df = query("""
        select p.person_id as employee_id, p.full_name, p.source as person_source
        from people p
        where p.person_id ilike %s or p.full_name ilike %s
        order by p.person_id limit %s""", (like, like, limit))
    df["full_name"] = display.apply(df.full_name, df.person_source == "pdl", display.proper_name)
    return df


def job_history(person_id):
    """Every job of a person, oldest first. PDL people (real people): current job only, and
    nothing that points to an earlier one."""
    df = query("""
        select e.employment_id, e.company_id, c.name as company, e.title, e.department,
               e.seniority, e.start_date, e.end_date, e.is_current,
               case when p.source = 'pdl' then null else e.change_type end as change_type,
               case when p.source = 'pdl' then null else e.previous_employment_id end
                   as previous_employment_id,
               e.source, p.source as person_source, c.source as company_source
        from employments e
        join companies c using (company_id)
        join people p using (person_id)
        where e.person_id = %s and (e.is_current or p.source <> 'pdl')
        order by e.start_date nulls first, e.employment_id""", (person_id,))
    df["title"] = display.apply(df.title, df.person_source == "pdl", display.proper_title)
    df["company"] = display.apply(df.company, df.company_source == "pdl", display.proper_title)
    return df.drop(columns=["person_source", "company_source"])


def demo_changes():
    """What "Reset demo data" would remove: counts only."""
    return query("""
        select (select count(*) from people where source = 'manual') as people,
               (select count(*) from employments where source = 'manual') as jobs,
               (select count(*) from companies where source = 'manual') as companies,
               (select count(*) from saved_queries) as queries,
               (select count(*) from change_log) as changes""").iloc[0].astype(int).to_dict()


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


def run_readonly(sql):
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
                conn.execute("set local app.show_pdl_names = 'on'")  # names are public
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
where e.person_id = 'N001-E0002'
order by e.start_date nulls first, e.employment_id""",
    "A company's current org chart": """select p.full_name, e.title, m.full_name as manager, rl.confidence, rl.reason
from reporting_lines rl
join people p      on p.person_id = rl.person_id
left join people m on m.person_id = rl.manager_id
left join employments e on e.person_id = rl.person_id
                       and e.company_id = rl.company_id and e.is_current
where rl.company_id = 'N001' and rl.is_current
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
    "People not placed (one company)": """select p.full_name, e.title, rl.reason
from reporting_lines rl
join people p on p.person_id = rl.person_id
left join employments e on e.person_id = rl.person_id
                       and e.company_id = rl.company_id and e.is_current
where rl.company_id = 'N001' and rl.is_current and rl.manager_id is null
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
where c.source not in ('pdl', 'pdl_history')
group by 1, 2
order by 1, 2""",
}
