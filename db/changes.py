"""
db/changes.py  -  Phase 2: add and change companies and people in the database.

History is never overwritten. A change CLOSES the person's current employments row
(end_date = today, is_current = false) and OPENS a new one (start_date = today), linked by
previous_employment_id. Every function marks each affected company needs_refresh = true
(the database trigger does too), so the next refresh re-infers exactly those.

Every change also writes one change_log row (in the same transaction) listing the rows it
opened and closed, so undo_last() can reverse it. judge = who places the changed people at
the next refresh: 'rules' (default) or 'gemini'.

Each function opens its own connection and commits, or joins a transaction when given
`conn=`. Each returns a dict of what it did (ids), so a change can be shown or undone.

  add_company(name, industry=None, country=None, source='manual')    -> company_id 'M001'
  add_person(full_name, company_id, title, department=None)          -> change_type 'new'
  change_title(person_id, new_title)                                 -> 'title_change'
  change_job(person_id, new_company_id, new_title=None)              -> 'job_change'
  remove_person(person_id, moving_to=None)  closes the job; moving_to (company id or name
                                            in the database) opens a 'job_change' there
  undo_last()                               reverses the newest change not undone yet
  reset_demo()                              removes everything created in the app
"""
from contextlib import contextmanager

from db import display
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


def _log(c, kind, done, judge="rules", summary=None):
    """One change_log row for this change; adds its change_id to done."""
    done["change_id"] = c.execute(
        "insert into change_log (kind, person_id, company_ids, opened, closed, added_people,"
        " added_companies, judge, summary) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "returning change_id",
        (kind, done.get("person_id"), done.get("companies", []), done.get("opened", []),
         done.get("closed", []), done.get("people", []),
         [done["company_id"]] if kind == "add_company" else [], judge, summary)).fetchone()[0]
    return done


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
        done = {"company_id": cid, "companies": [cid]}
        _log(c, "add_company", done, summary=f"new organisation {name.strip()} ({cid})")
    return done


def add_person(full_name, company_id, title, department=None, judge="rules", conn=None):
    with _tx(conn) as c:
        if not _company_exists(c, company_id):
            raise ValueError(f"company {company_id} not found")
        pid = _next_id(c, "people", "person_id", "M-P", 4)
        c.execute("insert into people (person_id, full_name, source) "
                  "values (%s, %s, 'manual')", (pid, full_name.strip()))
        opened = _open(c, pid, company_id, title.strip(), department, None, "new", None)
        _mark(c, company_id)
        done = {"person_id": pid, "people": [pid], "opened": [opened], "closed": [],
                "companies": [company_id]}
        _log(c, "add_person", done, judge,
             f"added {full_name.strip()} ({title.strip()}) to {company_id}")
    return done


def change_title(person_id, new_title, judge="rules", conn=None):
    with _tx(conn) as c:
        job = _current_job(c, person_id)
        if (job["title"] or "").strip().lower() == new_title.strip().lower():
            raise ValueError("the new title is the same as the current one")
        _close(c, job["employment_id"])
        opened = _open(c, person_id, job["company_id"], new_title.strip(), job["department"],
                       job["location"], "title_change", job["employment_id"])
        _mark(c, job["company_id"])
        done = {"person_id": person_id, "opened": [opened], "closed": [job["employment_id"]],
                "companies": [job["company_id"]]}
        old_title = (display.proper_title(job["title"]) if person_id.startswith("P-")
                     else job["title"])
        _log(c, "change_title", done, judge,
             f"{person_id}: {old_title} → {new_title.strip()}")
    return done


def change_job(person_id, new_company_id, new_title=None, judge="rules", conn=None):
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
        done = {"person_id": person_id, "opened": [opened], "closed": [job["employment_id"]],
                "companies": [job["company_id"], new_company_id]}
        _log(c, "change_job", done, judge,
             f"{person_id}: moved {job['company_id']} → {new_company_id}")
    return done


def remove_person(person_id, moving_to=None, judge="rules", conn=None):
    """Close the current job. moving_to: a company id or name in the database -> a
    'job_change' there with the same title; otherwise the person just leaves."""
    with _tx(conn) as c:
        job = _current_job(c, person_id)
        _close(c, job["employment_id"])
        _mark(c, job["company_id"])
        done = {"person_id": person_id, "opened": [], "closed": [job["employment_id"]],
                "companies": [job["company_id"]], "moved_to": None}
        if moving_to:
            row = c.execute("select company_id from companies where source <> 'pdl_history' "
                            "and (company_id = %s or lower(name) = lower(%s)) "
                            "order by company_id limit 1",
                            (moving_to.strip(), moving_to.strip())).fetchone()
            if row and row[0] != job["company_id"]:
                done["opened"].append(_open(c, person_id, row[0], job["title"],
                                            job["department"], None, "job_change",
                                            job["employment_id"]))
                _mark(c, row[0])
                done["companies"].append(row[0])
                done["moved_to"] = row[0]
        where = (f" → {done['moved_to']}" if done["moved_to"]
                 else f" (moving to {moving_to.strip()}, not in the database)" if moving_to
                 else "")
        _log(c, "remove_person", done, judge,
             f"{person_id} left {job['company_id']}{where}")
    return done


def save_query(name, sql, conn=None):
    """Save (or replace) a query for the app's SQL tab."""
    with _tx(conn) as c:
        c.execute("insert into saved_queries (name, sql) values (%s, %s) "
                  "on conflict (name) do update set sql = excluded.sql, created_at = now()",
                  (name.strip(), sql.strip()))


def last_change(conn=None):
    """The newest change not undone yet, as a dict, or None."""
    with _tx(conn) as c:
        row = c.execute("select change_id, kind, summary, created_at from change_log "
                        "where undone_at is null order by change_id desc limit 1").fetchone()
    return dict(zip(["change_id", "kind", "summary", "created_at"], row)) if row else None


def undo_last(conn=None):
    """Reverse the newest change not undone yet:
      - rows it opened: deleted, or closed if a refresh already used them (history stays)
      - rows it closed: open again (is_current = true, no end date)
      - people / companies it added: deleted if nothing refers to them any more
      - every affected company is marked needs_refresh
    Returns the change_log row (dict). Raises ValueError if there is nothing to undo or the
    data changed since (e.g. the reopened job would clash with a newer current job)."""
    with _tx(conn) as c:
        row = c.execute("select change_id, kind, summary, company_ids, opened, closed, "
                        "added_people, added_companies from change_log "
                        "where undone_at is null order by change_id desc limit 1 "
                        "for update").fetchone()
        if not row:
            raise ValueError("Nothing to undo.")
        ch = dict(zip(["change_id", "kind", "summary", "company_ids", "opened", "closed",
                       "added_people", "added_companies"], row))
        for eid in ch["opened"]:
            used = c.execute("select 1 from reporting_lines where employment_id = %s limit 1",
                             (eid,)).fetchone()
            if used:                                   # a refresh used it: keep as history
                c.execute("update employments set is_current = false, end_date = "
                          "greatest(current_date, coalesce(start_date, current_date)) "
                          "where employment_id = %s and is_current", (eid,))
            else:
                c.execute("delete from employments where employment_id = %s", (eid,))
        for eid in ch["closed"]:
            clash = c.execute(
                "select 1 from employments e join employments o "
                "on o.person_id = e.person_id and o.company_id = e.company_id "
                "where e.employment_id = %s and o.is_current and o.employment_id <> %s",
                (eid, eid)).fetchone()
            if clash:
                raise ValueError("Can't undo: this person has a newer job at that company.")
            c.execute("update employments set is_current = true, end_date = null "
                      "where employment_id = %s", (eid,))
        for pid in ch["added_people"]:                 # only if nothing refers to them
            c.execute("delete from people p where p.person_id = %s "
                      "and not exists (select 1 from employments e where e.person_id = %s) "
                      "and not exists (select 1 from reporting_lines r where r.person_id = %s"
                      " or r.manager_id = %s or r.proposed_manager_id = %s)",
                      (pid, pid, pid, pid, pid))
        for cid in ch["added_companies"]:
            c.execute("delete from companies co where co.company_id = %s "
                      "and not exists (select 1 from employments e where e.company_id = %s) "
                      "and not exists (select 1 from reporting_lines r "
                      "where r.company_id = %s)", (cid, cid, cid))
        _mark(c, *ch["company_ids"])
        c.execute("update change_log set undone_at = now() where change_id = %s",
                  (ch["change_id"],))
    return ch


def reset_demo(conn=None):
    """Back to the loaded data, in one transaction: deletes every job, person, company and
    saved query created in the app and the change log, and reopens the loaded jobs the app
    closed. Marks the affected companies for a refresh. Run history (runs) is kept.
    Returns counts and the affected company ids."""
    with _tx(conn) as c:
        c.execute("set local lock_timeout = '10s'")
        c.execute("lock table companies, people, employments, reporting_lines, change_log, "
                  "saved_queries in share row exclusive mode")     # no change can slip in
        reopen = c.execute(
            "select e.employment_id, e.company_id from employments e where e.source <> 'manual' "
            "and not e.is_current and e.employment_id in "
            "(select unnest(closed) from change_log)").fetchall()
        jobs = c.execute("select employment_id, company_id, is_current from employments "
                         "where source = 'manual'").fetchall()
        people = [r[0] for r in c.execute(
            "select person_id from people where source = 'manual'").fetchall()]
        orgs = [r[0] for r in c.execute(
            "select company_id from companies where source = 'manual'").fetchall()]
        job_ids = [r[0] for r in jobs]
        lines = c.execute(
            "delete from reporting_lines where employment_id = any(%s) or person_id = any(%s) "
            "or manager_id = any(%s) or proposed_manager_id = any(%s) or company_id = any(%s) "
            "returning company_id, is_current", (job_ids, people, people, people, orgs)
        ).fetchall()
        c.execute("delete from employments where employment_id = any(%s)", (job_ids,))
        c.execute("update employments set is_current = true, end_date = null "
                  "where employment_id = any(%s)", ([r[0] for r in reopen],))
        c.execute("delete from people where person_id = any(%s)", (people,))
        c.execute("delete from companies where company_id = any(%s)", (orgs,))
        queries = c.execute("delete from saved_queries").rowcount
        logged = c.execute("delete from change_log").rowcount
        affected = ({r[1] for r in reopen} | {r[1] for r in jobs if r[2]}
                    | {r[0] for r in lines if r[1]}) - set(orgs)
        c.execute("update companies set needs_refresh = true where company_id = any(%s)",
                  (sorted(affected),))
    return {"people": len(people), "jobs_removed": len(jobs), "jobs_reopened": len(reopen),
            "companies_removed": len(orgs), "queries": queries, "changes": logged,
            "companies": sorted(affected)}
