"""
scripts/run_sql.py  -  Phase 2: database. Runs one .sql file (e.g. a migration) on the
database. Prints only "done" or the error (never the address or password).
Run from the repo root:  python scripts/run_sql.py db/migrations/001_pdl_history.sql
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                     # so "db" can be imported
from db.connect import get_connection  # noqa: E402

path = Path(sys.argv[1])
with get_connection(autocommit=True) as conn:     # the file has its own begin / commit
    conn.execute(path.read_text(encoding="utf-8"))
print(f"done: {path}")
