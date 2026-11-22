"""
scripts/refresh_watch.py  -  runs refresh.py (rules only) and measures the database size while
it runs. Stops the refresh if the database passes the limit (450 MB).
Companies are saved one at a time, so a stopped refresh keeps the companies already done.
Prints sizes and refresh.py's counts. Exit code 1 if the limit was passed or the refresh failed.

Run from the repo root:  python scripts/refresh_watch.py
  --all   first marks every PDL company for a refresh (to measure a full refresh)
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db.connect import get_connection  # noqa: E402

LIMIT_BYTES = 450_000_000
EVERY_SECONDS = 3


def main():
    with get_connection(autocommit=True) as conn:
        size = lambda: conn.execute("select pg_database_size(current_database())").fetchone()[0]
        start = peak = size()
        print(f"before: {start / 1e6:.1f} MB", flush=True)
        if "--all" in sys.argv:
            conn.execute("update companies set needs_refresh = true "
                         "where source = 'pdl_company'")
        job = subprocess.Popen([sys.executable, "refresh.py"], cwd=ROOT)
        over = False
        while job.poll() is None:
            now = size()
            peak = max(peak, now)
            if now > LIMIT_BYTES:
                over = True
                job.terminate()
                job.wait(timeout=30)
                break
            time.sleep(EVERY_SECONDS)
        end = size()
        peak = max(peak, end)
    print(f"after: {end / 1e6:.1f} MB; peak during the refresh: {peak / 1e6:.1f} MB "
          f"({peak / 1048576:.1f} MiB)", flush=True)
    if over:
        sys.exit("STOPPED: the database passed 450 MB during the refresh.")
    if job.returncode:
        sys.exit(f"The refresh failed (exit code {job.returncode}).")


if __name__ == "__main__":
    main()
