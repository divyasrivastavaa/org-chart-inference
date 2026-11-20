"""
scripts/pdl_strip_cache.py  -  Phase 2: PDL. Rewrites the saved responses in
data/pdl/cache/ so they keep only the fields this project uses (see strip_person in
pdl_test.py): no emails, phone numbers, social links or addresses. No API call.
Before overwriting a file, checks that pdl_load.py reads exactly the same jobs from the
stripped version. Safe to rerun (a stripped file stays the same).
Run from the repo root:  python scripts/pdl_strip_cache.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdl_load import company_row, jobs_of  # noqa: E402
from pdl_test import CACHE, PERSON_FIELDS, strip_saved  # noqa: E402


def loaded(p):
    """Exactly what pdl_load.py writes to the database for one person's jobs."""
    return [(company_row(j["company"], ""), j["title"], j["department"], j["seniority"],
             j["start"], j["end"], j["current"]) for j in jobs_of(p)]


for path in sorted(CACHE.glob("person_search_*.json")):
    saved = json.loads(path.read_text(encoding="utf-8"))
    stripped = strip_saved(saved)
    old, new = saved["response"].get("data", []), stripped["response"].get("data", [])
    same = all(loaded(a) == loaded(b) and a.get("id") == b.get("id")
               and a.get("full_name") == b.get("full_name") for a, b in zip(old, new))
    if not same or len(old) != len(new):
        sys.exit(f"Stopped: {path.name} would lose data the loader needs; not changed")
    kept = len(PERSON_FIELDS) + 1                # + experience
    removed = sorted({k for p in old for k in p} - {k for p in new for k in p})
    before = path.stat().st_size
    path.write_text(json.dumps(stripped, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{path.name}: {len(new)} people, kept {kept} top-level fields, removed "
          f"{len(removed)}; {before / 1024:,.0f} KB -> {path.stat().st_size / 1024:,.0f} KB")
