"""
scripts/pdl_load_leaders.py  -  Phase 2: Atlassian's top leaders, so the 50 PDL people can
get a manager. At most ONE Person Search call (size 5, so at most 5 credits).

Asks only for the CEO, the Chief Revenue Officer and the Chief People and AI Enablement
Officer named on atlassian.com/company/people. The response is saved to data/pdl/cache/
straight away; a rerun reads that file and never calls PDL again. An error is saved too and
is never retried.

A person is loaded only if exactly one returned profile has their name, works at Atlassian
now, and has a title the rules read as the expected level and function. Stores the same
limited fields as scripts/pdl_load.py. The key is never printed.

Run from the repo root:  python scripts/pdl_load_leaders.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import infer  # noqa: E402
import pdl_load  # noqa: E402
import refresh  # noqa: E402
from pdl_test import cache_file, call_once  # noqa: E402

MAX_CREDITS = 5
ATLASSIAN = "P-Jav0LdTpq88xDlVrf56yGwEiRghj"     # PDL key (company_row); in the database: P01
LEADERS = {  # who -> (name spellings, expected level, expected function)
    "CEO": (["mike cannon-brookes", "michael cannon-brookes", "mike cannon brookes",
             "michael cannon brookes"], 1, "Executive"),
    "Chief Revenue Officer": (["brian duffy"], 2, "Sales"),
    "Chief People and AI Enablement Officer": (["avani prabhakar"], 2, "People"),
}
TITLE_FIX = {"chief revenue officer (cro)": "chief revenue officer"}
REQUEST = {"query": {"bool": {"must": [
    {"term": {"job_company_website": "atlassian.com"}},
    {"terms": {"full_name": [n for names, _, _ in LEADERS.values() for n in names]}}]}},
    "size": MAX_CREDITS}


def get_response():
    path = cache_file(REQUEST)
    if path.exists():
        print(f"Read from cache: {path.name} (no API call, no credits spent)")
        return json.loads(path.read_text(encoding="utf-8"))
    saved = call_once(REQUEST, max_size=MAX_CREDITS)     # exactly one call, never retried
    path.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"ONE API call made. Saved to {path.name}")
    return saved


def tidy(p):
    """PDL repeats the abbreviation in this title; store it as Atlassian lists it. Only in
    memory: the cached response stays exactly as PDL sent it."""
    p["job_title"] = TITLE_FIX.get(p.get("job_title"), p.get("job_title"))
    for e in p.get("experience") or []:
        title = e.get("title") or {}
        title["name"] = TITLE_FIX.get(title.get("name"), title.get("name"))


def works_here_now(p):
    jobs = pdl_load.jobs_of(p)
    return {pdl_load.company_row(j["company"], "")[0] for j in jobs if j["current"]} == {ATLASSIAN}


def choose(people):
    """Only the profiles that are clearly the three leaders."""
    chosen = []
    for p in people:
        tidy(p)
    for who, (names, level, function) in LEADERS.items():
        same = [p for p in people if (p.get("full_name") or "").lower() in names]
        fits = []
        for p in same:
            dept = refresh.PDL_DEPARTMENTS.get(p.get("job_title_role"))
            got = infer.parse_title(p.get("job_title"), dept)[:2]
            ok = got == (level, function) and works_here_now(p)
            print(f"  {who}: PDL title '{p.get('job_title')}' -> level {got[0]}, {got[1]}"
                  f"{'' if ok else ' (does not match, not loaded)'}")
            if ok:
                fits.append(p)
        if len(fits) == 1:
            chosen.append(fits[0])
        else:
            print(f"  {who}: {len(same)} profile(s), {len(fits)} match: NOT loaded")
    print(f"Loading {len(chosen)} of {len(people)} returned\n")
    return chosen


def main():
    try:
        saved = get_response()
    except Exception as e:                              # e.g. no network: stop, no retry
        sys.exit(f"Stopped: the call failed ({type(e).__name__}). Not retried.")
    if saved["status"] != 200:
        h = saved["headers"]
        sys.exit(f"Stopped: PDL answered HTTP {saved['status']}. Not retried. Credits spent: "
                 f"{h.get('x-call-credits-spent', 'not reported')}, left: "
                 f"{h.get('x-totallimit-remaining', 'not reported')}. "
                 + json.dumps(saved["response"])[:400])
    pdl_load.main(saved, keep=choose)


if __name__ == "__main__":
    main()
