"""
scripts/pdl_test.py  -  Phase 2: PDL person API test. ONE call, 2 people, then stop.

- Looks in data/pdl/cache/ first. If this exact request was already made, it reads the
  saved file and makes NO API call (no credits spent).
- Otherwise it makes exactly one Person Search call (size = 2) and saves the JSON, the
  HTTP status and the credit / rate-limit response headers to data/pdl/cache/. Only the
  fields this project uses are saved (strip_person): no emails, phone numbers, social
  links or addresses.
- Prints which fields came back for each person, and the credit headers.
The key is read from .streamlit/secrets.toml (or st.secrets) and is never printed.

Run from the repo root:  python scripts/pdl_test.py
"""
import hashlib
import json
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "pdl" / "cache"
URL = "https://api.peopledatalabs.com/v5/person/search"
# One well-known company, by website (more exact than the name); only people with a title
REQUEST = {"query": {"bool": {"must": [{"term": {"job_company_website": "atlassian.com"}},
                                       {"exists": {"field": "job_title"}}]}},
           "size": 2}
MAX_SIZE = 2                                     # hard limit for this test: 2 people


def api_key():
    path = ROOT / ".streamlit" / "secrets.toml"
    if path.exists():
        with open(path, "rb") as f:
            secrets = tomllib.load(f)
    else:
        import streamlit as st
        secrets = st.secrets
    if not secrets.get("PDL_API_KEY"):
        sys.exit("PDL_API_KEY is not set in .streamlit/secrets.toml")
    return secrets["PDL_API_KEY"]


PERSON_FIELDS = ["id", "full_name", "job_title", "job_title_role", "job_title_sub_role",
                 "job_title_levels", "job_start_date", "job_end_date", "job_company_id",
                 "job_company_name", "job_company_website", "job_company_industry",
                 "job_company_size"]


def strip_company(c):
    if not isinstance(c, dict):
        return c
    out = {k: c.get(k) for k in ("id", "name", "industry", "size", "website")}
    out["location"] = {"country": (c.get("location") or {}).get("country")}
    return out


def strip_person(p):
    """Only the fields this project uses: name, title, company, dates, department,
    seniority and job history. No emails, phone numbers, social links or addresses."""
    out = {k: p.get(k) for k in PERSON_FIELDS}
    out["experience"] = [
        {"company": strip_company(e.get("company")),
         "title": {k: (e.get("title") or {}).get(k) for k in ("name", "role", "sub_role",
                                                               "levels")},
         "start_date": e.get("start_date"), "end_date": e.get("end_date"),
         "is_primary": e.get("is_primary")}
        for e in p.get("experience") or []]
    return out


def strip_saved(saved):
    """A saved response with only the needed fields (error responses are kept as they are)."""
    resp = saved["response"]
    if saved["status"] == 200 and isinstance(resp.get("data"), list):
        saved = {**saved, "response": {"total": resp.get("total"),
                                       "data": [strip_person(p) for p in resp["data"]]}}
    return saved


def cache_file(request=REQUEST):
    body = json.dumps(request, sort_keys=True)
    return CACHE / f"person_search_{hashlib.sha256((URL + body).encode()).hexdigest()[:16]}.json"


def call_once(request=REQUEST, max_size=MAX_SIZE):
    """Exactly one API call. Keeps only response headers about credits / limits."""
    assert request["size"] <= max_size, "size above the credit budget"
    req = urllib.request.Request(URL, data=json.dumps(request).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Api-Key": api_key()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, headers, body = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:          # errors are saved too: no silent retry
        status, headers, body = e.code, dict(e.headers), e.read()
    keep = {k: v for k, v in headers.items()
            if any(w in k.lower() for w in ("credit", "limit", "remaining", "usage"))}
    try:
        data = json.loads(body)
    except ValueError:
        data = {"raw": body.decode(errors="replace")}
    return strip_saved({"request": request, "status": status, "headers": keep,
                        "response": data})          # only the needed fields are ever saved


def show(v):
    if v in (None, "", [], {}):
        return "MISSING"
    if v is True:                                # PDL free plan: "exists, value hidden"
        return "hidden (true)"
    return str(v)[:60]


def main():
    path = cache_file()
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        print(f"Read from cache: {path.relative_to(ROOT)} (no API call, no credits spent)")
    else:
        saved = call_once()
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"ONE API call made. Saved to {path.relative_to(ROOT)}")

    print(f"HTTP status: {saved['status']}")
    print("Credit / limit headers:", json.dumps(saved["headers"], indent=2) or "none")
    resp = saved["response"]
    if saved["status"] != 200:
        print("Error response:", json.dumps(resp, indent=2)[:800])
        return
    people = resp.get("data", [])
    print(f"People returned: {len(people)} (total matches: {resp.get('total')})\n")

    for i, p in enumerate(people, 1):
        exp = p.get("experience") or []
        past = [e for e in exp if not e.get("is_primary")]
        print(f"Person {i}")
        rows = [
            ("full name", "present" if p.get("full_name") else show(p.get("full_name"))),
            ("job title", show(p.get("job_title"))),
            ("company", show(p.get("job_company_name"))),
            ("start date", show(p.get("job_start_date"))),
            ("end date (current job)", show(p.get("job_end_date"))),
            ("seniority / level", show(p.get("job_title_levels"))),
            ("department (role)", show(p.get("job_title_role"))),
            ("department (sub role)", show(p.get("job_title_sub_role"))),
            ("past jobs (experience)", f"{len(exp)} entries, {len(past)} past"
             if exp else "MISSING"),
        ]
        if exp:
            e = exp[-1]                          # one entry: which of its fields are filled
            rows.append(("  one entry has", ", ".join(
                k for k, v in [("company", (e.get("company") or {}).get("name")),
                               ("title", (e.get("title") or {}).get("name")),
                               ("start", e.get("start_date")), ("end", e.get("end_date"))]
                if v not in (None, "", True)) or "nothing filled"))
        for k, v in rows:
            print(f"  {k:<26} {v}")
        print()


if __name__ == "__main__":
    main()
