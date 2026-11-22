"""
scripts/compact_db.py  -  keeps the database tidy without passing 450 MB.

Default: a normal VACUUM (ANALYZE) of every table. It marks the space of deleted rows as free,
so new rows reuse it. It needs no extra copy of a table, and never makes the database bigger.

--full: also tries to give space back to the disk, one table at a time. REINDEX and VACUUM FULL
briefly keep an old and a new copy, so a table is SKIPPED unless the current size plus a full
extra copy of it stays under the safe limit (440 MB, 10 MB below the 450 MB limit).

A second connection watches the size every second and cancels the running step if the database
passes 450 MB; a cancelled step changes nothing, and the script stops with an error.
Prints sizes and times only.
Run from the repo root:  python scripts/compact_db.py [--full]
"""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db.connect import get_connection  # noqa: E402

LIMIT_BYTES = 450_000_000
SAFE_BYTES = 440_000_000
TABLES = ["sources", "companies", "answer_key", "people", "employments", "reporting_lines"]
SIZE = "select pg_database_size(current_database())"


def rebuild(work, label, command, table, size_query):
    """Runs one copying step, or skips it if the extra copy would not fit."""
    now = work.execute(SIZE).fetchone()[0]
    extra = work.execute(size_query, (table,)).fetchone()[0]
    if now + extra > SAFE_BYTES:
        print(f"skipped {label}, {table}: {now / 1e6:.1f} MB + a {extra / 1e6:.1f} MB copy "
              f"would pass {SAFE_BYTES / 1e6:.0f} MB", flush=True)
        return
    began = time.time()
    work.execute(command.format(table))                    # table names fixed in this file
    print(f"{label}, {table}: {work.execute(SIZE).fetchone()[0] / 1e6:.1f} MB "
          f"[{time.time() - began:.1f} s]", flush=True)


def main():
    peak = [0]
    over = threading.Event()
    stop = threading.Event()
    with get_connection(autocommit=True) as work, get_connection(autocommit=True) as watch:
        work.execute("set statement_timeout = 0")

        def watcher():
            while not stop.is_set():
                now = watch.execute(SIZE).fetchone()[0]
                peak[0] = max(peak[0], now)
                if now > LIMIT_BYTES:
                    over.set()
                    work.cancel()
                    return
                time.sleep(1)

        thread = threading.Thread(target=watcher, daemon=True)
        thread.start()
        try:
            began = time.time()
            for t in TABLES:
                work.execute(f"vacuum (analyze) {t}")
            print(f"after VACUUM: {work.execute(SIZE).fetchone()[0] / 1e6:.1f} MB "
                  f"[{time.time() - began:.1f} s]", flush=True)
            if "--full" in sys.argv:
                for t in TABLES:
                    rebuild(work, "indexes rebuilt", "reindex table {}", t,
                            "select pg_indexes_size(%s::regclass)")
                for t in TABLES:
                    rebuild(work, "compacted", "vacuum (full, analyze) {}", t,
                            "select pg_total_relation_size(%s::regclass)")
        except Exception as e:
            stop.set()
            if over.is_set():
                sys.exit("STOPPED: the database passed 450 MB; the running step was cancelled.")
            sys.exit(f"Compaction failed: {type(e).__name__}")
        stop.set()
        thread.join(timeout=5)
        final = work.execute(SIZE).fetchone()[0]
    print(f"final: {final / 1e6:.1f} MB ({final / 1048576:.1f} MiB); highest size seen: "
          f"{peak[0] / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
