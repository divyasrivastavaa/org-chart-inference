"""
scripts/pdl_record_runs.py  -  Phase 2: cost tracking. Records each PDL API call we made as
its own row in runs (trigger 'pdl_import'), using only the numbers saved in
data/pdl/cache/ (credits spent, people returned, time). No API call.

- started_at / finished_at: the call's rate-limit window from the saved headers
  (x-ratelimit-reset minus 1 minute); close to when the call was made.
- cost_usd: credits x the price per credit in db/costs.py (free plan: $0).
- note: the cache file name, so a rerun never records the same call twice.
Run from the repo root:  python scripts/pdl_record_runs.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db import costs  # noqa: E402
from db.connect import get_connection  # noqa: E402

CACHE = ROOT / "data" / "pdl" / "cache"

with get_connection() as conn:
    for path in sorted(CACHE.glob("person_search_*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        h = saved["headers"]
        credits = int(h.get("x-call-credits-spent", 0))
        people = len(saved["response"].get("data", []))
        reset = h.get("x-ratelimit-reset")
        when = (datetime.strptime(reset, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                - timedelta(minutes=1)) if reset else datetime.now(timezone.utc)
        added = conn.execute(
            "insert into runs (started_at, finished_at, trigger, status, companies_processed,"
            " people_processed, pdl_credits_used, cost_usd, note) "
            "values (%s, %s, 'pdl_import', %s, 1, %s, %s, %s, %s) "
            "on conflict (note) where trigger = 'pdl_import' do nothing",
            (when, when, "succeeded" if saved["status"] == 200 else "failed", people,
             credits, costs.run_cost(pdl_credits=credits), path.name)).rowcount
        print(f"{path.name}: {credits} credits, {people} people -> "
              + ("recorded" if added else "already recorded, skipped"))
