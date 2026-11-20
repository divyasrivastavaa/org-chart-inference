"""
db/changes.py  -  Phase 2: add and change companies and people in the database.

History is never overwritten. A change CLOSES the person's current employments row
(end_date = today, is_current = false) and OPENS a new one (start_date = today), linked by
previous_employment_id. Every function marks each affected company needs_refresh = true
(the database trigger does too), so the next `python refresh.py` re-infers exactly those.

Each function opens its own connection and commits, or joins a transaction when given
`conn=`. Each returns a dict of what it did (ids), so a change can be shown or undone.

  add_company(name, industry=None, country=None, source='manual')    -> company_id 'M001'
  add_person(full_name, company_id, title, department=None)          -> change_type 'new'
  change_title(person_id, new_title)                                 -> 'title_change'
  change_job(person_id, new_company_id, new_title=None)              -> 'job_change'
  remove_person(person_id, moving_to=None)  closes the job; moving_to (company id or name
                                            in the database) opens a 'job_change' there
"""
from contextlib import contextmanager

from db.connect import get_connection


@contextmanager
def _tx(conn):
    if conn is None:
        with get_connection() as c:              # commits when the block succeeds
            yield c
    else:
        with conn.transaction():
            yield conn


def _mark(c, *company_ids):
    c.execute("update companies set needs_refresh = true where company_id = any(%s)",
              (list(company_ids),))


def _next_id(c, table, column, prefix, width):
    """Next free id like M001 / M-P0001 (prefix + zero-padded number)."""
    n = c.execute(f"select max(substring({column} from %s)::int) from {table} "   # fixed names
                  f"where {column} ~ %s", (len(prefix) + 1, f"^{prefix}[0-9]+$")).fetchone()[0]
    return f"{prefix}{(n or 0) + 1:0{width}d}"


def _company_exists(c, company_id):
    return c.execute("select 1 from companies where company_id = %s",
                     (company_id,)).fetchone() is not None


def _current_job(c, person_id):
    rows = c.execute("select employment_id, company_id, title, department, location "
                     "from employments where person_id = %s and is_current",
                     (person_id,)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"{person_id} has {len(rows)} current jobs (need exactly 1)")
    return dict(zip(["employment_id", "company_id", "title", "department", "location"],
                    rows[0]))


def _close(c, employment_id):
    # end_date = today (never before the start date, which the table doesn't allow)
    c.execute("update employments set is_current = false, "
              "end_date = greatest(current_date, coalesce(start_date, current_date)) "
              "where employment_id = %s", (employment_id,))


def _open(c, person_id, company_id, title, department, location, change_type, previous):
    return c.execute(
        "insert into employments (person_id, company_id, title, department, location, "
        "start_date, is_current, change_type, previous_employment_id, source) "
        "values (%s, %s, %s, %s, %s, current_date, true, %s, %s, 'manual') "
        "returning employment_id",
        (person_id, company_id, title, department, location, change_type,
         previous)).fetchone()[0]


def add_company(name, industry=None, country=None, source="manual", conn=None):
    with _tx(conn) as c:
        cid = _next_id(c, "companies", "company_id", "M", 3)
        c.execute("insert into companies (company_id, name, industry, country, source, "
                  "needs_refresh) values (%s, %s, %s, %s, %s, true)",
                  (cid, name.strip(), industry, country, source))
    return {"company_id": cid, "companies": [cid]}


def add_person(full_name, company_id, title, department=None, conn=None):
    with _tx(conn) as c:
        if not _company_exists(c, company_id):
            raise ValueError(f"company {company_id} not found")
        pid = _next_id(c, "people", "person_id", "M-P", 4)
        c.execute("insert into people (person_id, full_name, source) "
                  "values (%s, %s, 'manual')", (pid, full_name.strip()))
        opened = _open(c, pid, company_id, title.strip(), department, None, "new", None)
        _mark(c, company_id)
    return {"person_id": pid, "people": [pid], "opened": [opened], "closed": [],
            "companies": [company_id]}


def change_title(person_id, new_title, conn=None):
    with _tx(conn) as c:
        job = _current_job(c, person_id)
        if (job["title"] or "").strip().lower() == new_title.strip().lower():
            raise ValueError("the new title is the same as the current one")
        _close(c, job["employment_id"])
        opened = _open(c, person_id, job["company_id"], new_title.strip(), job["department"],
                       job["location"], "title_change", job["employment_id"])
        _mark(c, job["company_id"])
    return {"person_id": person_id, "opened": [opened], "closed": [job["employment_id"]],
            "companies": [job["company_id"]]}


def change_job(person_id, new_company_id, new_title=None, conn=None):
    """Move to another company (title kept when new_title is None). Marks both companies."""
    with _tx(conn) as c:
        job = _current_job(c, person_id)
        if new_company_id == job["company_id"]:
            raise ValueError("same company: use change_title")
        if not _company_exists(c, new_company_id):
            raise ValueError(f"company {new_company_id} not found")
        _close(c, job["employment_id"])
        opened = _open(c, person_id, new_company_id, (new_title or job["title"]).strip(),
                       job["department"], None, "job_change", job["employment_id"])
        _mark(c, job["company_id"], new_company_id)
    return {"person_id": person_id, "opened": [opened], "closed": [job["employment_id"]],
            "companies": [job["company_id"], new_company_id]}


def remove_person(person_id, moving_to=None, conn=None):
    """Close the current job. moving_to: a company id or name in the database -> a
    'job_change' there with the same title; otherwise the person just leaves."""
    with _tx(conn) as c:
        job = _current_job(c, person_id)
        _close(c, job["employment_id"])
        _mark(c, job["company_id"])
        done = {"person_id": person_id, "opened": [], "closed": [job["employment_id"]],
                "companies": [job["company_id"]], "moved_to": None}
        if moving_to:
            row = c.execute("select company_id from companies where company_id = %s "
                            "or lower(name) = lower(%s) order by company_id limit 1",
                            (moving_to.strip(), moving_to.strip())).fetchone()
            if row and row[0] != job["company_id"]:
                done["opened"].append(_open(c, person_id, row[0], job["title"],
                                            job["department"], None, "job_change",
                                            job["employment_id"]))
                _mark(c, row[0])
                done["companies"].append(row[0])
                done["moved_to"] = row[0]
    return done
