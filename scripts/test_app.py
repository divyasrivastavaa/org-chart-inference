"""
scripts/test_app.py  -  Phase 2: app test. Runs the real app.py without a browser
(Streamlit AppTest) against the database and checks:
  - front page per dataset: precision / coverage = the numbers measured from the database
    (E9: Benchmark 95.8% / 63.1%, UK 84.5% / 18.8%, ASX/NZX 100.0% / 83.7%)
  - company pages (Benchmark, UK with sources, ASX/NZX, PDL with names masked)
  - Database view: job history, runs & cost, SQL (examples run; writes and set_config
    refused)
  - no database -> the "Database not connected" message, no crash
Read only: nothing is written. No PDL calls.
Run from the repo root:  python scripts/test_app.py
"""
import sys
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db import connect, read as dbread  # noqa: E402

failures = []


def check(ok, label):
    print(("  ok    " if ok else "  FAIL  ") + label)
    if not ok:
        failures.append(label)


def new_app():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    return at


def metrics(at):
    return {m.label: m.value for m in at.metric}


def no_errors(at, label):
    check(not at.exception, f"{label}: no exception")
    if at.exception:
        print("        ", at.exception[0].message[:300])


# ---------------------------------------------------------------- front page
print("Front page")
at = new_app()
at.run()
no_errors(at, "front page, all datasets")
m = metrics(at)
print("        ", m)
check(m.get("Companies") == "533", "533 companies (500 + 12 + 20 + 1 PDL)")
expected = {"synthetic": ("95.8%", "63.1%"), "uk": ("84.5%", "18.8%"),
            "real": ("100.0%", "83.7%")}
for key, (prec, cov) in expected.items():
    at.session_state["datasets"] = [key]
    at.run()
    m = metrics(at)
    check((m.get("Precision"), m.get("Coverage")) == (prec, cov),
          f"{key}: precision {m.get('Precision')} (expected {prec}), "
          f"coverage {m.get('Coverage')} (expected {cov})")
at.session_state["datasets"] = ["pdl"]
at.run()
m = metrics(at)
check(m.get("Coverage") == "—" and m.get("People") == "50", f"pdl only: {m}")

# ---------------------------------------------------------------- company pages
print("Company pages")
for cid, what in [("C001", "Benchmark"), ("U01", "UK"), ("R01", "ASX/NZX"),
                  ("P-" , "PDL")]:
    at = new_app()
    if cid == "P-":                                   # the PDL company (Atlassian)
        cid = dbread.query("select company_id from companies where source = 'pdl'"
                           ).company_id[0]
    at.session_state["company_select"] = cid
    at.run()
    no_errors(at, f"{what} {cid}")
    m = metrics(at)
    print("        ", {k: m[k] for k in m if k in ("Employees", "Coverage", "Precision")})
    tables = [t.value for t in at.dataframe]
    people = next((t for t in tables if "Name" in t.columns and "Status" in t.columns), None)
    check(people is not None and len(people) > 0, f"{what}: People tab has rows")
    if what == "PDL":
        check(people.Name.str.startswith("PDL person #").all(),
              "PDL: every name masked without the PIN")
    if what == "UK":
        check(any("Organogram file on data.gov.uk" in md.value for md in at.markdown),
              "UK: sources listed under the chart (from the database)")
    if what == "ASX/NZX":
        check(any("leadership page" in md.value for md in at.markdown),
              "ASX/NZX: sources listed")
    if what == "Benchmark":
        exp = dbread.accuracy(0.7, ["C001"])
        check(m.get("Precision") == f"{exp['precision']:.1%}",
              f"C001 precision {m.get('Precision')} = database {exp['precision']:.1%}")

# ---------------------------------------------------------------- Database view
print("Database view")
at = new_app()
at.session_state["db_view"] = True
at.run()
no_errors(at, "Database view")
check([t.label for t in at.tabs][:3] == ["Job history", "Runs & cost", "SQL"], "3 tabs")
at.text_input[0].set_value("C001-E0002").run()
no_errors(at, "job history search")
hist = [t.value for t in at.dataframe if "change_type" in t.value.columns]
check(bool(hist) and len(hist[0]) >= 1, "job history shows the person's jobs")
m = metrics(at)
check("Runs" in m and "Total cost" in m, f"runs & cost totals: {m}")

sql_box = next(b for b in at.selectbox if b.label == "Example queries")
for name in dbread.EXAMPLES:
    sql_box.set_value(name).run()
    run_btn = next(b for b in at.button if b.label == "Run query")
    run_btn.click().run()
    ok = not at.exception and not at.warning and not [e for e in at.error]
    rows = [t.value for t in at.dataframe]
    check(ok, f"example '{name}' runs ({len(rows[-1]) if rows else 0} rows shown)")
    sql_box = next(b for b in at.selectbox if b.label == "Example queries")

for bad, why in [("delete from runs", "write"),
                 ("select set_config('role', 'postgres', true)", "set_config"),
                 ("select * from auth.users", "auth schema"),
                 ("select 1; select 2", "two statements")]:
    sql_box.set_value("(write your own)").run()
    at.text_area[-1].set_value(bad).run()
    next(b for b in at.button if b.label == "Run query").click().run()
    check(bool(at.warning) or bool(at.error), f"refused: {why}")
    sql_box = next(b for b in at.selectbox if b.label == "Example queries")

# ---------------------------------------------------------------- no database
print("No database")
real = connect.get_connection


def broken(**kwargs):
    raise RuntimeError("no database (test)")


connect.get_connection = broken
st.cache_data.clear()
try:
    at = new_app()
    at.run()
    check(not at.exception, "no crash without a database")
    check(any("Database not connected" in e.value for e in at.error),
          "shows 'Database not connected'")
finally:
    connect.get_connection = real
    st.cache_data.clear()

print(f"\n{'ALL PASSED' if not failures else str(len(failures)) + ' FAILED'}")
sys.exit(1 if failures else 0)
