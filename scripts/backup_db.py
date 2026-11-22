"""
scripts/backup_db.py  -  Exports every table of the database to data/backup/<time>/ (git-ignored).

Read only. All tables come from one consistent snapshot (a REPEATABLE READ, READ ONLY
transaction), one gzip-compressed CSV per table, plus:
  structure.json  indexes, constraints, policies, triggers and functions (for reference)
  manifest.json   rows and file size per table
After writing, each file is read back and its rows are counted. Exits with an error if any
count differs from the database. Prints only table names and counts.

Run from the repo root:  python scripts/backup_db.py
"""
import csv
import gzip
import json
import sys
from datetime import datetime
from pathlib import Path

import psycopg
import psycopg.sql

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db.connect import get_connection  # noqa: E402

csv.field_size_limit(10_000_000)

STRUCTURE = {
    "indexes": "select tablename, indexname, indexdef from pg_indexes "
               "where schemaname = 'public' order by 1, 2",
    "constraints": "select conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
                   "from pg_constraint where connamespace = 'public'::regnamespace order by 1, 2",
    "policies": "select tablename, policyname, roles::text, cmd, qual, with_check "
                "from pg_policies where schemaname = 'public' order by 1, 2",
    "triggers": "select tgrelid::regclass::text, tgname, pg_get_triggerdef(oid) from pg_trigger "
                "where not tgisinternal order by 1, 2",
    "functions": "select proname, pg_get_functiondef(oid) from pg_proc "
                 "where pronamespace = 'public'::regnamespace and prokind = 'f' order by 1",
}


def count_rows(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.reader(f)) - 1            # minus the header


def main():
    out = ROOT / "data" / "backup" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True)
    manifest, failed = {}, []
    with get_connection() as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("select pg_database_size(current_database())")
            db_bytes = cur.fetchone()[0]
            cur.execute("select tablename from pg_tables where schemaname = 'public' order by 1")
            tables = [r[0] for r in cur.fetchall()]
            for t in tables:
                name = psycopg.sql.Identifier(t)
                cur.execute(psycopg.sql.SQL("select count(*) from {}").format(name))
                n_db = cur.fetchone()[0]
                path = out / f"{t}.csv.gz"
                query = psycopg.sql.SQL("copy (select * from {}) to stdout with (format csv, "
                                        "header true)").format(name)
                with gzip.open(path, "wb", compresslevel=6) as f, cur.copy(query) as copy:
                    for block in copy:
                        f.write(block)
                n_file = count_rows(path)
                manifest[t] = {"rows_database": n_db, "rows_file": n_file,
                               "file_bytes": path.stat().st_size}
                ok = n_db == n_file
                failed += [] if ok else [t]
                print(f"{'ok  ' if ok else 'FAIL'} {t:<16} database {n_db:>8,}  file {n_file:>8,}")
            structure = {}
            for key, sql in STRUCTURE.items():
                cur.execute(sql)
                structure[key] = [list(r) for r in cur.fetchall()]
        conn.rollback()
    (out / "structure.json").write_text(json.dumps(structure, indent=1, default=str),
                                        encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(
        {"created": datetime.now().isoformat(timespec="seconds"), "database_bytes": db_bytes,
         "tables": manifest}, indent=1), encoding="utf-8")
    size = sum(p.stat().st_size for p in out.iterdir())
    print(f"\n{len(tables)} tables, {size / 1e6:.1f} MB on disk, in data/backup/{out.name}")
    if failed:
        sys.exit(f"BACKUP FAILED: counts differ for {failed}")
    print("BACKUP CONFIRMED: every row count matches the database")


if __name__ == "__main__":
    main()
