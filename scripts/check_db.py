"""
scripts/check_db.py  -  Phase 2: database. Checks the connection, read only.
Prints only the Postgres version, the tables in the public schema and their row counts
(never the address or password).
Run from the repo root:  python scripts/check_db.py
"""
import sys
from pathlib import Path

from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "db" can be imported
from db.connect import get_connection  # noqa: E402

with get_connection() as conn:
    conn.read_only = True                        # this script never changes anything
    print(conn.execute("select version()").fetchone()[0])
    tables = [r[0] for r in conn.execute(
        "select table_name from information_schema.tables "
        "where table_schema = 'public' and table_type = 'BASE TABLE' "
        "order by table_name").fetchall()]
    print(f"\n{len(tables)} tables in the public schema:")
    for t in tables:
        n = conn.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(t))).fetchone()[0]
        print(f"  {t:<16} {n:>8,} rows")
