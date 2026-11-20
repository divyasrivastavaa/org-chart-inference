"""
scripts/undo_demo.py  -  Phase 2: puts the database back as it was before
scripts/demo_changes.py, using what the demo wrote to data/demo_changes.json.

In one transaction (all or nothing):
  1. deletes the demo's reporting lines and makes the lines that were current before the
     demo current again;
  2. deletes the employments rows the demo opened and reopens the ones it closed;
  3. deletes the people (and companies) the demo added;
  4. puts back needs_refresh / last_refreshed_at / last_run_id of the companies, and
     deletes the demo's runs rows.
Then deletes data/demo_changes.json.
  --dry-run  does all of it, prints the counts, then rolls back (nothing changes).
Run from the repo root:  python scripts/undo_demo.py [--dry-run]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db.connect import get_connection  # noqa: E402

RECORD = ROOT / "data" / "demo_changes.json"


def main(dry_run):
    if not RECORD.exists():
        sys.exit("Nothing to undo (no data/demo_changes.json).")
    rec = json.loads(RECORD.read_text(encoding="utf-8"))
    opened = [i for s in rec["steps"] for i in s.get("opened", [])]
    closed = [i for s in rec["steps"] for i in s.get("closed", [])]
    people = [p for s in rec["steps"] for p in s.get("people", [])]
    added_cos = [s["company_id"] for s in rec["steps"] if s["what"] == "add company"]
    runs = rec["run_ids"]
    done = {}
    conn = get_connection()
    try:
        with conn.transaction():
            ex = conn.execute
            done["reporting lines deleted"] = ex(
                "delete from reporting_lines where run_id = any(%s)", (runs,)).rowcount
            done["reporting lines made current again"] = ex(
                "update reporting_lines set is_current = true where line_id = any(%s)",
                (rec["before"]["lines"],)).rowcount
            done["employments deleted"] = ex(
                "delete from employments where employment_id = any(%s)", (opened,)).rowcount
            done["employments reopened"] = ex(
                "update employments set is_current = true, end_date = null "
                "where employment_id = any(%s)", (closed,)).rowcount
            done["people deleted"] = ex("delete from people where person_id = any(%s)",
                                        (people,)).rowcount
            done["companies deleted"] = ex(
                "delete from companies where company_id = any(%s)", (added_cos,)).rowcount
            for cid, needs, refreshed, last_run in rec["before"]["companies"]:
                ex("update companies set needs_refresh = %s, last_refreshed_at = %s, "
                   "last_run_id = %s where company_id = %s", (needs, refreshed, last_run, cid))
            done["runs deleted"] = ex("delete from runs where run_id = any(%s)",
                                      (runs,)).rowcount
            for k, v in done.items():
                print(f"  {k:<36} {v}")
            if dry_run:
                raise _DryRun                      # an error inside the block = rollback
    except _DryRun:
        print("\nDry run: rolled back, nothing changed.")
        return
    finally:
        conn.close()
    RECORD.unlink()
    print("\nUndone: the database is back to how it was before the demo.")


class _DryRun(Exception):
    pass


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
