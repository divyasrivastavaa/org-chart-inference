"""
scripts/pdl_load.py  -  Phase 2: PDL people. ONE Person Search call (or the cache), then
load the people and their job history into the database.

Request: senior people (job_title_levels cxo / vp / director) currently at Atlassian,
size 50. Credit budget: 50. The call is skipped when the response is already in
data/pdl/cache/ (a rerun never spends credits), and refused when it could cost more than
the budget or more than the credits left (from the last saved response headers).

Loads:
  companies    Atlassian (source 'pdl', needs_refresh = true) + every past employer in
               their job history (source 'pdl_history', needs_refresh = false)
  people       one row each (source 'pdl'); person_id = 'P-' + PDL id
  employments  every job in their experience, oldest first: 'initial' for the earliest,
               'job_change' when the company changes, 'title_change' when only the title
               changes. The current job has is_current = true. previous_employment_id links
               each job to the one before it.
Stores only name, title, company, dates, department and seniority (no emails, phone
numbers or social links). Safe to rerun: people already in the database are skipped.
The key is never printed.

Run from the repo root:  python scripts/pdl_load.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from db.connect import get_connection  # noqa: E402
from pdl_test import CACHE, cache_file, call_once  # noqa: E402

BUDGET = 50                                      # most credits this script may spend
REQUEST = {"query": {"bool": {"must": [
    {"term": {"job_company_website": "atlassian.com"}},
    {"terms": {"job_title_levels": ["cxo", "vp", "director"]}}]}},
    "size": 50}


def credits_left():
    """Credits left according to the newest saved response, or None if unknown."""
    files = sorted(CACHE.glob("person_search_*.json"), key=lambda f: f.stat().st_mtime)
    for f in reversed(files):
        left = json.loads(f.read_text(encoding="utf-8"))["headers"].get("x-totallimit-remaining")
        if left is not None:
            return int(left)
    return None


def get_response():
    path = cache_file(REQUEST)
    if path.exists():
        print(f"Read from cache: {path.relative_to(ROOT)} (no API call, no credits spent)")
        return json.loads(path.read_text(encoding="utf-8"))
    left = credits_left()
    if REQUEST["size"] > BUDGET:
        sys.exit(f"Stopped: size {REQUEST['size']} is above the budget of {BUDGET} credits")
    if left is not None and REQUEST["size"] > left:
        sys.exit(f"Stopped: size {REQUEST['size']} is above the {left} credits left")
    saved = call_once(REQUEST, max_size=BUDGET)
    path.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"ONE API call made. Saved to {path.relative_to(ROOT)}")
    return saved


def to_date(s):
    """PDL dates: '2022', '2022-09' or '2022-09-15' -> 'YYYY-MM-DD' (missing parts = 01)."""
    if not isinstance(s, str) or not s[:4].isdigit():
        return None
    parts = (s.split("-") + ["01", "01"])[:3]
    return "-".join([parts[0], parts[1].zfill(2), parts[2].zfill(2)])


def company_row(c, source):
    """(company_id, name, industry, size_band, country, website, source, source_ref)"""
    ref = c.get("id")
    cid = f"P-{ref}" if ref else "P-n-" + "-".join(c["name"].lower().split())
    loc = c.get("location") or {}
    return (cid, c["name"], c.get("industry"), c.get("size"), loc.get("country"),
            c.get("website"), source, ref)


def jobs_of(p):
    """The person's jobs, oldest first, current job last. Only the fields we need."""
    jobs = []
    for e in p.get("experience") or []:
        c, t = e.get("company") or {}, e.get("title") or {}
        if not c.get("name"):
            continue                              # no company: can't place this job
        jobs.append({"company": c, "title": t.get("name"),
                     "department": t.get("role") or None,
                     "seniority": ",".join(t.get("levels") or []) or None,
                     "start": to_date(e.get("start_date")), "end": to_date(e.get("end_date")),
                     "current": bool(e.get("is_primary")) and not e.get("end_date")})
    if not any(j["current"] for j in jobs) and p.get("job_company_name"):
        jobs.append({"company": {"id": p.get("job_company_id"), "name": p["job_company_name"],
                                 "industry": p.get("job_company_industry"),
                                 "size": p.get("job_company_size"),
                                 "website": p.get("job_company_website")},
                     "title": p.get("job_title"), "department": p.get("job_title_role"),
                     "seniority": ",".join(p.get("job_title_levels") or []) or None,
                     "start": to_date(p.get("job_start_date")), "end": None, "current": True})
    for j in jobs:                                # year-only dates can put end before start
        if j["start"] and j["end"] and j["end"] < j["start"]:
            j["end"] = j["start"]
    return sorted(jobs, key=lambda j: (j["current"], j["start"] or j["end"] or ""))


def table_counts(conn):
    return {t: conn.execute(f"select count(*) from {t}").fetchone()[0]   # fixed names
            for t in ["companies", "people", "employments"]}


def main():
    saved = get_response()
    print(f"HTTP status: {saved['status']}")
    h = saved["headers"]
    if saved["status"] != 200:
        sys.exit("Error response: " + json.dumps(saved["response"])[:800])
    people = saved["response"].get("data", [])
    print(f"People returned: {len(people)} (total matches: {saved['response'].get('total')})")
    print(f"Credits spent by this request: {h.get('x-call-credits-spent', '?')}, "
          f"credits left after it: {h.get('x-totallimit-remaining', '?')}\n")

    found, skipped_people, repeats = Counter(), 0, 0
    with get_connection() as conn:               # one transaction: all or nothing
        before = table_counts(conn)
        existing = {r[0] for r in conn.execute("select person_id from people").fetchall()}
        for p in people:
            pid = f"P-{p['id']}"
            if pid in existing:
                skipped_people += 1
                continue
            jobs = jobs_of(p)
            current_ids = {company_row(j["company"], "pdl")[0] for j in jobs if j["current"]}
            for j in jobs:
                j["company_id"] = company_row(j["company"], "")[0]
                current = j["company_id"] in current_ids   # also for an earlier stint there
                row = company_row(j["company"], "pdl" if current else "pdl_history")
                # A past employer that turns out to be someone's current company becomes 'pdl'
                conn.execute(
                    "insert into companies (company_id, name, industry, size_band, country, "
                    "website, source, source_ref, needs_refresh) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "on conflict (company_id) do update set source = 'pdl' "
                    "where excluded.source = 'pdl' and companies.source = 'pdl_history'",
                    (*row, current))
            conn.execute("insert into people (person_id, full_name, source, source_ref) "
                         "values (%s, %s, 'pdl', %s)", (pid, p.get("full_name"), p["id"]))
            prev = None
            for j in jobs:
                if prev is None:
                    kind = "initial"
                elif j["company_id"] != prev["company_id"]:
                    kind = "job_change"
                elif j["title"] != prev["title"]:
                    kind = "title_change"
                else:
                    repeats += 1                  # same company and title again: skip
                    continue
                found[kind] += 1
                j["employment_id"] = conn.execute(
                    "insert into employments (person_id, company_id, title, department, "
                    "seniority, start_date, end_date, is_current, change_type, "
                    "previous_employment_id, source) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pdl') "
                    "returning employment_id",
                    (pid, j["company_id"], j["title"], j["department"], j["seniority"],
                     j["start"], j["end"], j["current"], kind,
                     prev["employment_id"] if prev else None)).fetchone()[0]
                prev = j
        conn.execute("update companies set needs_refresh = true "
                     "where company_id in (select distinct company_id from employments "
                     "where source = 'pdl' and is_current)")        # Atlassian
        after = table_counts(conn)
        refresh = conn.execute("select name, source from companies where needs_refresh "
                               "and source like 'pdl%'").fetchall()
        by_source = conn.execute("select source, count(*) from companies "
                                 "where source like 'pdl%' group by source").fetchall()

    print("Rows added per table:")
    for t in after:
        print(f"  {t:<12} +{after[t] - before[t]:,}  (now {after[t]:,})")
    print("PDL companies in the database: " + ", ".join(f"{s}: {n}" for s, n in by_source))
    print(f"\nJobs loaded: {sum(found.values())}  ->  initial (earliest job): {found['initial']}, "
          f"job changes: {found['job_change']}, title changes: {found['title_change']}")
    print(f"Repeated entries skipped (same company and title as the job before): {repeats}")
    if skipped_people:
        print(f"People already in the database, skipped: {skipped_people}")
    print("PDL companies marked needs_refresh: " + ", ".join(n for n, _ in refresh))


if __name__ == "__main__":
    main()
