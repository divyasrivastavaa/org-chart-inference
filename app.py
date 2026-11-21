"""
app.py  -  Step 7 + Phase 2: database
Streamlit app. Everything it shows is read from the Postgres database (db/read.py). The
CSV files are only used once, to import the source data (scripts/load_db.py).
Front page: one searchable, sortable table of all companies from all datasets
("Benchmark": generated, "ASX/NZX leadership" and "UK government organograms": real,
"PDL people": real people from People Data Labs), with a Dataset column and "Add people".
Click a company name (or use the sidebar) to open that company's inferred org chart.
Company page: add, remove, promote and undo. Changes are saved in the database for everyone;
"Reset demo data" in the sidebar puts the loaded data back.
Sidebar "Database": job history, runs & cost, read-only SQL.
Run locally with:  streamlit run app.py
"""
import json
import re
from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st

import org_chart
import refresh
import whatif                    # only its title checks (check_title, in_context)
from db import changes, read as dbread
from db.connect import get_connection

st.set_page_config(page_title="Org Chart Inference", layout="wide")
ROOT = Path(__file__).resolve().parent
# Internal key -> name shown in the app. Names say where the data comes from.
DATASETS = {"synthetic": "Benchmark", "real": "ASX/NZX leadership",
            "uk": "UK government organograms", "pdl": "PDL people (real)",
            "manual": "Added in the app"}
GEN, ASX, UKN, PDL = (DATASETS[k] for k in ("synthetic", "real", "uk", "pdl"))
OGL = ("Contains public sector information licensed under the "
       "[Open Government Licence v3.0]"
       "(https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).")
MODEL_THRESHOLD = json.loads((ROOT / "models" / "confidence_tree.json").read_text())[
    "decision_threshold"]
UK_TUNE = {"U02", "U03", "U07", "U09", "U10", "U11"}   # UK fixes were designed on these
PDL_MONTHLY_CREDITS = 100                               # free plan
MAX_ADD = 20                                            # people per "Add people"
TTL = 60                                                # seconds a database read is cached
NOT_CONNECTED = ("**Database not connected.** The app reads everything from the database. "
                 "Check DATABASE_URL and DATABASE_PASSWORD in the Streamlit secrets, or "
                 "resume the Supabase project if it was paused, then reload this page.")


def db(fn, *args):
    """Call a database read; if the database can't be reached, say so and stop."""
    try:
        return fn(*args)
    except (psycopg.Error, RuntimeError):
        st.error(NOT_CONNECTED)
        st.stop()


def plain(text):
    """Text typed by a visitor, safe to show to everyone in a Markdown element: no links,
    images, formatting or colour directives. (An invisible space stops bare web and email
    addresses from turning into links.)"""
    text = re.sub(r"(?i)(www)\.", "\\1\u200b.", str(text).replace("://", ":\u200b//"))
    text = text.replace("@", "\u200b@")
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>~:$=^&])", r"\\\1", str(text))


def flash(kind, text):
    """A message shown once at the top of the next page (after a change or a refresh)."""
    st.session_state.setdefault("flash", []).append((kind, text))


def saved():
    """After any write. Company and front-page caches need nothing: their key (the versions
    in dbread.page_state) has changed. Only these two have no version."""
    db_runs.clear()
    db_accuracy.clear()


# Cached by version (dbread.page_state): reused until the data really changes. max_entries
# keeps memory small on the free hosting plan.
@st.cache_data(max_entries=8, show_spinner=False)
def db_companies(version):
    return dbread.companies()


@st.cache_data(max_entries=24, show_spinner=False)
def db_stats(threshold, version):
    return dbread.company_stats(threshold)


@st.cache_data(max_entries=8, show_spinner=False)
def db_counts(version):
    return dbread.counts()


@st.cache_data(ttl=TTL, show_spinner=False)
def db_accuracy(threshold, company_ids):
    return dbread.accuracy(threshold, list(company_ids))


@st.cache_data(max_entries=40, show_spinner=False)
def db_company(company_id, version):
    return dbread.company(company_id)


@st.cache_data(max_entries=40, show_spinner=False)
def db_pending(company_id, version):
    return dbread.pending_changes(company_id)


@st.cache_data(max_entries=40, show_spinner=False)
def db_history(company_id, version):
    return dbread.last_two_refreshes(company_id)


@st.cache_data(max_entries=40, show_spinner=False)
def company_context(company_id, version, _people):
    return whatif.in_context(_people)


# The built pages
@st.cache_data(max_entries=60, show_spinner=False)
def chart_html(company_id, version, threshold, show_truth, func, pick, _view):
    return org_chart.render_html(_view, show_truth, pick)


@st.cache_data(max_entries=40, show_spinner=False)
def levels_html(company_id, version, threshold, _people):
    return org_chart.render_levels(_people, LEVELS)


@st.cache_data(ttl=TTL, show_spinner=False)
def load_sources():
    """Where each real company / organisation comes from (table sources)."""
    return dbread.sources()


@st.cache_data(ttl=TTL, show_spinner=False)
def db_runs():
    return dbread.runs()


def when(dates):
    """First recorded date of some source rows, e.g. "14 Nov 2026"."""
    dates = pd.to_datetime(pd.Series(dates), errors="coerce").dropna()
    return f"{dates.iloc[0]:%d %b %Y}" if len(dates) else "date not recorded"


def apply_threshold(frame, threshold):
    """Lines below the threshold are not drawn: the manager is left blank."""
    frame = frame.copy()
    frame["manager_id"] = frame.pred_manager_id.where(frame.confidence >= threshold)
    if "reason" in frame:
        below = frame.pred_manager_id.notna() & frame.manager_id.isna()
        frame.loc[below, "reason"] = "confidence below threshold"
    frame["given"] = frame.manager_id.notna()
    frame["right"] = frame.given & (frame.manager_id == frame.true_manager_id)
    return frame


def score(frame):
    """Coverage and precision, as defined in evaluate.py."""
    return frame.given.mean(), frame.right.sum() / max(frame.given.sum(), 1)


def pct(x, digits=1):
    return "—" if pd.isna(x) else f"{x:.{digits}%}"


ss = st.session_state
ss.setdefault("table_version", 0)

# ---------------------------------------------------------------- all datasets
# Company IDs never clash (C001, R01, U01, P-...), so one table holds all of them.
wanted = ss.get("open_next") or ss.get("company_select") or st.query_params.get("company", "")
try:
    STATE = dbread.page_state(wanted)             # what changed? (one small query per page)
    FRONT_V = STATE["front"]
    companies = db_companies(FRONT_V)
except (psycopg.Error, RuntimeError):            # no secrets, wrong password, paused project
    st.title("Org Chart Inference")
    st.error(NOT_CONNECTED)
    st.stop()
companies = (companies[["company_id", "company_name", "industry", "dataset", "employees"]]
             .sort_values("company_name").reset_index(drop=True))
COUNTS = db(db_counts, FRONT_V)
company_label = dict(zip(companies.company_id,
                         companies.company_name + " (" + companies.company_id + ")"))
dataset_of = dict(zip(companies.company_id, companies.dataset))
# Organisations created in the app (saved in the database, source 'manual')
NEW_ORGS = {c for c, d in dataset_of.items() if d == "manual"}
st.query_params.pop("data", None)  # old links (?data=real&company=R01) still open R01

# ---------------------------------------------------------------- which company is open?
# One source of truth: ss.company_select (also the sidebar dropdown's value).
# Table clicks and the Back button write to it; the URL (?company=C001) mirrors it.
if ss.get("company_select") not in [""] + list(company_label):
    from_url = st.query_params.get("company", "")
    ss.company_select = from_url if from_url in company_label else ""
if ss.get("open_next"):            # set by the "Add people" window: open that company
    ss.company_select = ss.pop("open_next")
if ss.pop("go_home", False):       # set by "Reset demo data": back to the front page
    ss.company_select = ""
table_key = f"company_table_{ss.table_version}"


def open_clicked():
    """A cell in the table was clicked: open the company if it was the name."""
    cells = ss[table_key]["selection"]["cells"]
    if cells and cells[0][1] == "company_name":
        ss.company_select = ss.table_ids[cells[0][0]]
    ss.table_version += 1          # fresh table next time, so the old click doesn't stick


def back_to_all():
    ss.company_select = ""
    ss.table_version += 1


# Added to docs/flow.svg in the app only (the README keeps the still picture): the arrows
# become dashed lines that move slowly in the arrow's direction, like data flowing.
# Both dash patterns (6+4 and 2+4) repeat every 30px, so the loop has no jump.
FLOW_ANIMATION = """<style>
  .ar { stroke-dasharray: 6 4; animation: flow 3s linear infinite; }
  .ar[stroke-dasharray] { stroke-dasharray: 2 4; }      /* the "re-runs" arrow: dotted */
  @keyframes flow { to { stroke-dashoffset: -30; } }
  @media (prefers-reduced-motion: reduce) { .ar { animation: none; } }
</style>"""


def ids_of(dataset, keep=lambda cid: True):
    return tuple(sorted(c for c, d in zip(companies.company_id, companies.dataset)
                        if d == dataset and keep(c)))


def flow_svg():
    """docs/flow.svg with its numbers replaced by live numbers from the database (the
    README keeps the still picture). The held-out seed 7 isn't in the database: that line
    stays as measured from files (docs/experiments.md)."""
    svg = (ROOT / "docs" / "flow.svg").read_text(encoding="utf-8")
    t = MODEL_THRESHOLD
    gen = db(db_accuracy, t, ids_of("synthetic"))
    ukt = db(db_accuracy, t, ids_of("uk", lambda c: c not in UK_TUNE))
    asx = db(db_accuracy, t, ids_of("real"))
    new = {
        r">\d[\d,]* companies, messy titles,<":
            f">{COUNTS['synthetic']['companies']:,} companies, messy titles,<",
        r">\d+ organisations, [\d,]+ posts<":
            f">{COUNTS['uk']['companies']} organisations, {COUNTS['uk']['people']:,} posts<",
        r">\d+ senior people at \d+ company,<":
            f">{COUNTS.get('pdl', {}).get('people', 0)} senior people at "
            f"{COUNTS.get('pdl', {}).get('companies', 0)} company,<",
        r">Benchmark seed 42: [^<]*<":
            f">Benchmark seed 42: {pct(gen['precision'])} prec., {pct(gen['coverage'])} cov.<",
        r">UK test half: [^<]*<":
            f">UK test half: {pct(ukt['precision'])}, {pct(ukt['coverage'])}<",
        r">ASX/NZX: [^<]*<":
            f">ASX/NZX: {asx['right']} of {asx['given']} right, "
            f"{pct(asx['coverage'], 0)} coverage<",
    }
    for pattern, text in new.items():
        svg = re.sub(pattern, text, svg)
    return svg


@st.dialog("How it works", width="large")
def docs_dialog():
    # The README's picture (docs/flow.svg), with the numbers taken from the database
    st.image(flow_svg().replace("</defs>", FLOW_ANIMATION + "\n  </defs>"), width="stretch")
    st.caption(f"Numbers come from the database: the current reporting lines (last "
               f"refresh) at threshold {MODEL_THRESHOLD}, scored against the answer key. "
               "The held-out seed 7 line is measured from files (docs/experiments.md, E5): "
               "that dataset is not in the database.")
    src = db(load_sources)
    key = src.what_it_shows.str.startswith("Confirms ")       # answer-key sources
    pages = src[(src.dataset == ASX) & ~key]
    uk = src[src.dataset == UKN]
    st.markdown("#### Sources")
    st.markdown(
        f"- **{GEN}:** generated by `generate_data.py`. No outside source: every company, "
        "name and title is made up, and the generator writes the answer key itself.\n"
        f"- **{UKN}:** the senior-staff organogram CSV of {len(uk)} organisations from "
        f"data.gov.uk (downloaded {when(uk.date_checked)}; "
        "raw files in `data/raw/uk/`, links in the database table `sources`). The published "
        "\"reports to\" column is the answer key. " + OGL + "\n"
        f"- **{ASX}:** each company's own leadership page ({len(pages)} pages, checked "
        f"{when(pages.date_checked)}). Its small answer key "
        f"({COUNTS['confirmed_lines']} confirmed lines) comes from {key.sum()} written "
        "company sources: announcements about the person, and annual reports or governance "
        "statements. All of them are in the database table `sources` and under the chart on "
        "each company's page; the exact quotes are in `scripts/build_real_truth.py`.\n"
        f"- **{PDL}:** {COUNTS.get('pdl', {}).get('people', 0)} senior people at one company, "
        "from the People Data Labs person API (cached; no answer key). The app shows their "
        "current job only.")


def set_flag(name, value):
    ss[name] = value


def what_it_used(r):
    """One run in words, for the Runs & cost table."""
    tokens = int(r.gemini_input_tokens + r.gemini_output_tokens)
    parts = []
    if r.pdl_credits_used or r.trigger == "pdl_import":
        parts.append(f"PDL – {int(r.pdl_credits_used)} credit"
                     + ("" if r.pdl_credits_used == 1 else "s"))
    if r.gemini_calls or tokens:
        parts.append(f"Gemini – {tokens:,} tokens")
    label = " + ".join(parts) or "rules – free"
    return label if r.status == "succeeded" else f"{label} ({r.status})"


def database_page():
    """Sidebar → Database: job history, runs & cost, and a read-only SQL box."""
    st.button("Back", icon=":material/arrow_back:", on_click=set_flag, args=("db_view", False))
    st.subheader("Database")
    t_jobs, t_runs, t_sql = st.tabs(["Job history", "Runs & cost", "SQL"])

    with t_jobs:
        text = st.text_input("Find a person by name or id",
                             placeholder="e.g. C001-E0002, or part of a name")
        st.caption("Every job a person has had, oldest first. A change never overwrites "
                   "anything: the old job is closed and a new one opened. For PDL people "
                   "(real people), only the current job is shown.")
        if text.strip():
            found = db(dbread.find_people, text)
            if found.empty:
                st.info("No one found.")
            else:
                label = dict(zip(found.employee_id, found.full_name.fillna("(no name)")
                                 + " (" + found.employee_id + ")"))
                pid = st.selectbox(f"Person ({len(found)} found, first 50)", list(label),
                                   format_func=label.get)
                hist = db(dbread.job_history, pid)
                kinds = hist.change_type.value_counts()
                st.caption("Current job only." if pid.startswith("P-") else
                           f"{len(hist)} job(s): " + ", ".join(
                               f"{n} {k.replace('_', ' ')}" for k, n in kinds.items()))
                st.dataframe(hist, hide_index=True, width="stretch")

    with t_runs:
        runs = db(db_runs)
        used = [what_it_used(r) for r in runs.itertuples()]
        started = pd.to_datetime(runs.started_at, utc=True)
        today = pd.Timestamp.now(tz="UTC")
        this_month = (started.dt.year == today.year) & (started.dt.month == today.month)
        credits = int(runs.pdl_credits_used[this_month].sum())
        tokens = int(runs.gemini_input_tokens.sum() + runs.gemini_output_tokens.sum())
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Runs", f"{len(runs):,}")
        r2.metric("PDL credits used this month", f"{credits:,} of {PDL_MONTHLY_CREDITS}",
                  delta=f"{max(PDL_MONTHLY_CREDITS - credits, 0):,} left", delta_color="off",
                  delta_arrow="off")
        r3.metric("Gemini calls", f"{int(runs.gemini_calls.sum()):,}",
                  delta=f"{tokens:,} tokens", delta_color="off", delta_arrow="off")
        r4.metric("Rules runs (free)", f"{sum(u.startswith('rules') for u in used):,}")
        st.caption("Newest first. A refresh re-infers only the companies that changed.")
        st.dataframe(pd.DataFrame({
            "What it used": used, "Companies": runs.companies_processed,
            "People": runs.people_processed, "Seconds": runs.seconds.astype(float),
            "When (UTC)": started.dt.strftime("%d %b %H:%M")}
            ).loc[started.sort_values(ascending=False).index],  # PDL rows were logged later
            hide_index=True, width="stretch", placeholder="—")

    with t_sql:
        mine = db(dbread.saved_queries)               # saved from this tab (database)
        queries = dict(dbread.EXAMPLES) | {f"Saved: {r.name}": r.sql for r in mine.itertuples()}
        pick = st.selectbox("Example queries", ["(write your own)"] + list(queries),
                            help="The 8 examples, then the queries saved here (\"Saved: …\").")
        sql = st.text_area("SQL: one SELECT or WITH statement",
                           value=queries.get(pick, "select * from companies"),
                           height=220, key=f"sql_{pick}")
        s1, s2 = st.columns([3, 1], vertical_alignment="bottom")
        name = s1.text_input("Name to save this query as", key="save_name",
                             placeholder="e.g. Engineering managers")
        if s2.button("Save query", icon=":material/save:", width="stretch"):
            _, problem = dbread.check_sql(sql)
            if not name.strip():
                st.warning("Give the query a name.")
            elif problem:
                st.warning(f"Not saved: {problem}")
            else:
                db(changes.save_query, name, sql)
                saved()
                st.success(f"Saved as \"{plain(name.strip())}\": it is in the dropdown now.")
        st.caption("Read only: runs as a role that can only read this project's tables, "
                   "inside a READ ONLY transaction, stopped after 5 seconds, at most 500 rows "
                   "(LIMIT 500 is added if the query has no LIMIT). PDL people's past jobs are "
                   "not visible here.")
        if st.button("Run query", type="primary", icon=":material/play_arrow:"):
            try:
                result = dbread.run_readonly(sql)
            except ValueError as e:                   # refused before it reached the database
                st.warning(str(e))
            except psycopg.errors.QueryCanceled:
                st.warning("Stopped: the query took longer than 5 seconds.")
            except psycopg.errors.InsufficientPrivilege:
                st.warning("Not allowed: only this project's tables can be read here.")
            except psycopg.Error as e:
                st.error("The database refused the query: "
                         + (str(e).strip().splitlines() or ["error"])[0])
            except RuntimeError:
                st.error(NOT_CONNECTED)
            else:
                st.caption(f"{len(result):,} row(s)")
                st.dataframe(result, hide_index=True, width="stretch")


def undo_last_change():
    """Undo the newest change saved in the database (any company)."""
    try:
        ch = changes.undo_last()
    except ValueError as e:
        flash("warning", str(e))
        return
    except (psycopg.Error, RuntimeError):
        flash("error", NOT_CONNECTED)
        return
    saved()
    flash("success", f"Undone: {plain(ch['summary'])}. Marked for refresh: "
                     f"{', '.join(ch['company_ids'])}.")


def refresh_now():
    """Sidebar: re-infer only the companies with saved changes, with the rules (free), and
    log the run. The public app never calls Gemini."""
    try:
        out = refresh.main("app_change")
    except (psycopg.Error, RuntimeError):
        flash("error", NOT_CONNECTED)
        return
    saved()
    n = len(out["companies"])
    flash("success" if n else "info",
          f"Refreshed {n} compan{'y' if n == 1 else 'ies'}"
          + (f" ({', '.join(out['companies'][:10])}{' …' if n > 10 else ''}): "
             f"{out['people']:,} people, run {out['run_id']}."
             if n else ": nothing was waiting.")
          + ("".join(f" ⓘ {x}" for x in out["notes"])))


@st.dialog("Reset demo data", on_dismiss=lambda: set_flag("resetting", False))
def reset_dialog():
    try:
        n = dbread.demo_changes()
    except (psycopg.Error, RuntimeError):
        st.error(NOT_CONNECTED)
        return
    st.write("This puts the loaded data back **for everyone**. It removes every change made "
             "in the app and can't be undone:")
    st.markdown(f"- {n['people']} added people and {n['companies']} added organisations\n"
                f"- {n['jobs']} jobs created in the app (people who left or moved go back to "
                "their original job)\n"
                f"- {n['queries']} saved queries and {n['changes']} entries in the change log")
    yes, no = st.columns(2)
    if no.button("Cancel", width="stretch"):
        ss.resetting = False
        st.rerun()
    if not yes.button("Yes, reset everything", type="primary", width="stretch"):
        return
    try:
        with st.spinner("Resetting and refreshing the affected companies…"):
            done = changes.reset_demo()
            st.cache_data.clear()
            try:
                refresh.main("app_change")
            except (psycopg.Error, RuntimeError):
                flash("warning", "The data was reset, but the refresh failed. Click \"Refresh "
                                 "changed companies\" in the sidebar.")
    except psycopg.errors.LockNotAvailable:
        st.warning("Someone else is saving a change right now. Please try again.")
        return
    except (psycopg.Error, RuntimeError):
        st.error(NOT_CONNECTED)
        return
    k = len(done["companies"])
    flash("success", f"Demo data reset: {done['people']} people, {done['companies_removed']} "
                     f"organisations and {done['queries']} saved queries removed; {k} "
                     f"compan{'y' if k == 1 else 'ies'} refreshed.")
    ss.resetting = False
    ss.go_home = True              # the open company may have been removed
    ss.highlight = None
    st.rerun()


def parse_people(table, text):
    """Rows of the editable table + lines of the pasted list -> [(name, designation)].
    A pasted line is "Name | Designation", or just "Designation"."""
    def clean(x):
        return x.strip() if isinstance(x, str) else ""
    rows = [(clean(n), clean(d)) for n, d in zip(table["Name (optional)"], table["Designation"])]
    for line in text.splitlines():
        if line.strip():
            name, _, desig = line.rpartition("|")
            rows.append((clean(name), clean(desig)))
    return [r for r in rows if r[0] or r[1]]


NEW_ORG = "+new"


# The popups stay open (ss.adding / ss.removing) until they finish or are closed with X
@st.dialog("Add people", width="large", on_dismiss=lambda: set_flag("adding", False))
def add_people_dialog():
    options = list(company_label) + [NEW_ORG]
    current = ss.company_select if ss.company_select in company_label else options[0]
    to = st.selectbox("Company", options, index=options.index(current), key="add_to",
                      format_func=lambda c: "+ Create a new organisation" if c == NEW_ORG
                      else company_label[c])
    org_name = st.text_input("Name of the new organisation") if to == NEW_ORG else ""
    one, paste = st.tabs(["One by one", "Paste a list"])
    with one:
        table = st.data_editor(
            pd.DataFrame({"Name (optional)": [""] * 4, "Designation": [""] * 4}),
            num_rows="dynamic", hide_index=True, width="stretch", key="add_table")
    with paste:
        text = st.text_area("One person per line: Name | Designation",
                            placeholder="Priya Shah | Chief Risk Officer\nData Engineer",
                            height=150, key="add_text")
    st.caption(f"Up to {MAX_ADD} people at a time. Saved in the database, with history, and "
               "marked \"manual\". They are placed at the next refresh (\"Refresh changed "
               "companies\" in the sidebar). They have no answer key, so they never count in "
               "precision.")
    if not st.button("Add and save them", type="primary"):
        return
    people = parse_people(table, text)
    taken = set() if to == NEW_ORG else db(dbread.names_in, to)
    names = [n for n, _ in people if n]
    if len(people) > MAX_ADD:
        st.error(f"Please add at most {MAX_ADD} people at a time (this list has {len(people)}).")
    elif to == NEW_ORG and not org_name.strip():
        st.error("Please give the new organisation a name.")
    elif not people:
        st.error("Please add at least one person.")
    elif any(not d for _, d in people):
        st.error("Every person needs a designation (job title).")
    elif set(names) & taken or len(names) != len(set(names)):
        st.error("These names are already in this company: "
                 + ", ".join(plain(n) for n in sorted((set(names) & taken)
                                                      | {n for n in names
                                                         if names.count(n) > 1})))
    else:
        try:
            with get_connection() as conn:            # one transaction: all saved, or none
                to_id = (changes.add_company(org_name.strip(), conn=conn)["company_id"]
                         if to == NEW_ORG else to)
                k = 1
                for name, desig in people:
                    while not name:                   # "Name (optional)": New hire 1, 2 …
                        name = f"New hire {k}" if f"New hire {k}" not in taken else ""
                        k += 1
                    taken.add(name)
                    changes.add_person(name, to_id, desig, conn=conn)
        except (psycopg.Error, RuntimeError):
            st.error(NOT_CONNECTED)
            return
        saved()
        flash("success", f"Saved: {len(people)} person(s) added to {to_id}. They are placed "
                         "at the next refresh.")
        ss.open_next = to_id       # open the company to show where they landed
        ss.adding = False
        st.rerun()


@st.dialog("Remove people", width="large", on_dismiss=lambda: set_flag("removing", False))
def remove_people_dialog(cid, co):
    label = dict(zip(co.employee_id, co.full_name + " — " + co.title.fillna("(no title)")))
    leaving = st.multiselect("Who is leaving?", list(label), format_func=label.get,
                             placeholder="Pick one or more people")
    moving = st.text_input("Moving to (optional)", placeholder="a company id or name, e.g. C002")
    st.caption("Saved in the database: their job here is closed (kept in their job history). "
               "If \"Moving to\" is a company in the database, they get a job there with the "
               "same title. Their team is placed again at the next refresh.")
    if not st.button("Remove them", type="primary", disabled=not leaving):
        return
    try:
        with get_connection() as conn:                # one transaction: all saved, or none
            done = [changes.remove_person(eid, moving.strip() or None, conn=conn)
                    for eid in leaving]
    except ValueError as e:
        st.error(str(e))
        return
    except (psycopg.Error, RuntimeError):
        st.error(NOT_CONNECTED)
        return
    saved()
    moved = sorted({d["moved_to"] for d in done if d["moved_to"]})
    flash("success", f"Saved: {len(done)} person(s) left {cid}"
          + (f" and moved to {', '.join(moved)}" if moved else "")
          + (f" (\"{plain(moving.strip())}\" is not in the database, so they just left)"
             if moving.strip() and not moved else "") + ".")
    ss.removing = False
    st.rerun()


@st.dialog("Promote / change title", width="large",
           on_dismiss=lambda: set_flag("changing", False))
def change_title_dialog(cid, co):
    label = dict(zip(co.employee_id, co.full_name + " — " + co.title.fillna("(no title)")))
    eid = st.selectbox("Who?", list(label), format_func=label.get, index=None,
                       placeholder="Pick a person")
    new_title = st.text_input("New job title", placeholder="e.g. VP Engineering")
    st.caption("Saved in the database: their current job is closed and a new one opened with "
               "the new title (both kept in their job history). They and their team are "
               "placed again at the next refresh. Never counted in precision.")
    if not st.button("Apply change", type="primary", disabled=not eid or not new_title.strip()):
        return
    problem = whatif.check_title(co, eid, new_title)
    if problem:
        st.warning(plain(problem))
        return
    try:
        changes.change_title(eid, new_title)
    except ValueError as e:
        st.warning(str(e))
        return
    except (psycopg.Error, RuntimeError):
        st.error(NOT_CONNECTED)
        return
    saved()
    flash("success", f"Saved: {plain(label[eid].split(' — ')[0])} is now "
                     f"\"{plain(new_title.strip())}\".")
    ss.changing = False
    st.rerun()


# ---------------------------------------------------------------- sidebar
st.sidebar.selectbox("Company", [""] + list(company_label), key="company_select",
                     format_func=lambda c: company_label.get(c, "All companies"))
threshold = st.sidebar.slider("Confidence threshold", 0.5, 1.0, MODEL_THRESHOLD, 0.05,
                              help="Confidence comes from a decision tree trained on a "
                                   "separate dataset. Default 0.7: a wrong line is treated "
                                   "as costing about 2.3 times a missing one, so a line is "
                                   "drawn when it is right at least 70% of the time.")
show_truth = st.sidebar.checkbox("Show accuracy vs. answer key", value=True,
                                 help="Off: hides the precision numbers.")
domain_slot = st.sidebar.container()      # the company page puts its "Domain" filter here

# ---------------------------------------------------------------- sidebar: Database
st.sidebar.divider()
st.sidebar.markdown("**Database**")
st.sidebar.button("Job history · Runs & cost · SQL", icon=":material/database:",
                  width="stretch", on_click=set_flag, args=("db_view", True))
waiting = STATE["waiting"]
st.sidebar.button("Refresh changed companies", icon=":material/sync:", width="stretch",
                  on_click=refresh_now,
                  help="Re-infers only the companies with saved changes and logs the run.")
st.sidebar.caption(f"{waiting} compan{'y' if waiting == 1 else 'ies'} waiting for a refresh.")
st.sidebar.button("Reset demo data", icon=":material/restart_alt:", width="stretch",
                  on_click=set_flag, args=("resetting", True),
                  help="Removes every change made in the app and puts the loaded data back.")

st.sidebar.divider()
if st.sidebar.button("Docs: how it works", icon=":material/account_tree:", width="stretch"):
    docs_dialog()

if ss.get("resetting"):
    reset_dialog()
elif ss.get("adding"):
    add_people_dialog()
cid = ss.company_select
if cid and cid != STATE["company_id"]:
    STATE = db(dbread.page_state, cid)
COMPANY_V = STATE["company"]
if cid:
    st.query_params["company"] = cid
else:
    st.query_params.pop("company", None)

st.title("Org Chart Inference")
for kind, text in ss.pop("flash", []):          # result of the last change / refresh
    getattr(st, kind)(text)
if ss.get("db_view"):
    database_page()
    st.stop()
REAL = bool(cid) and dataset_of[cid] == "real"
UK = bool(cid) and dataset_of[cid] == "uk" and cid not in NEW_ORGS
PDLCO = bool(cid) and dataset_of[cid] == "pdl"
who = ("people with a confirmed manager" if REAL
       else "people (excluding the top post)" if UK
       else "people with a known manager (none: no answer key)" if PDLCO
       else "people (excluding CEOs)")
answer_key = ("the companies' written statements" if REAL
              else "the published reporting lines" if UK else "the answer key")

# ================================================================ front page: all companies
if not cid:
    st.button("Add people", icon=":material/add:", on_click=set_flag, args=("adding", True),
              help="Add people to a company, or to a new organisation (saved in the database)")

    who = "people who can be checked (no answer key: PDL people and people added in the app)"
    answer_key = "the answer key of each dataset"
    shown = companies
    # Per company from the database: lines drawn, people scored, given a line, right
    table = shown.merge(db(db_stats, threshold, FRONT_V), on="company_id", how="left")
    n_scored, n_given, n_right = (int(table[c].fillna(0).sum())
                                  for c in ("scored", "given", "right"))
    coverage = n_given / n_scored if n_scored else None
    precision = n_right / n_given if n_given else None
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Companies", f"{len(shown):,}")
    c2.metric("People", f"{int(shown.employees.sum()):,}")
    c3.metric("Coverage", pct(coverage),
              help=f"All companies: share of {who} who got a manager "
                   f"({n_scored:,} people can be checked)")
    if show_truth:
        c4.metric("Precision", pct(precision),
                  help=f"All companies: of the reporting lines that can be checked, the "
                       f"share that match {answer_key}")

    table["lines"] = table.lines.fillna(0)
    table["coverage"] = 100 * table.given / table.scored.where(table.scored > 0)
    table["precision"] = 100 * table.right / table.given.where(table.given > 0)
    # Largest companies first (ties stay A-Z); header clicks can still re-sort
    table = table.sort_values("employees", ascending=False, kind="stable").reset_index(drop=True)
    ss.table_ids = table.company_id.tolist()      # row number -> company, for open_clicked
    table["dataset"] = table.dataset.map(DATASETS.get)
    columns = ["company_name", "dataset", "industry", "employees", "lines", "coverage"] + \
        (["precision"] if show_truth else [])
    st.dataframe(
        table, key=table_key, on_select=open_clicked, selection_mode="single-cell",
        hide_index=True, width="stretch", height=640,
        column_order=columns,
        column_config={
            "company_name": st.column_config.TextColumn(
                "Company", help="Click a name to open its org chart"),
            "dataset": st.column_config.TextColumn("Dataset"),
            "industry": st.column_config.TextColumn("Industry"),
            "employees": st.column_config.NumberColumn("Employees", format="%d"),
            "lines": st.column_config.NumberColumn(
                "Lines drawn", format="%d",
                help="Reporting lines drawn at the current threshold"),
            "coverage": st.column_config.NumberColumn("Coverage", format="%.0f%%"),
            "precision": st.column_config.NumberColumn("Precision", format="%.1f%%"),
        })
    st.stop()

# ================================================================ company page
# This company from the database: the current lines (last refresh), plus people saved since
# then (waiting for a refresh), scored rows and the answer key. Nothing is kept in memory.
df, scored, truth_small = db(db_company, cid, COMPANY_V)
co = apply_threshold(df, threshold)
# A manager who left or moved since the last refresh is no longer on this chart: their team
# shows as not placed until the next refresh
gone_mgr = co.manager_id.notna() & ~co.manager_id.isin(set(co.employee_id))
co.loc[gone_mgr, ["manager_id", "reason"]] = [None, "manager left (waiting for refresh)"]
co["given"] = co.manager_id.notna()
co["right"] = co.given & (co.manager_id == co.true_manager_id)
co["judge"] = co.judge.map({"gemini": "Gemini"})   # the chart marks "placed by Gemini"
left_ids, interim_ids, vacant_ids, not_here = set(), set(), set(), set()   # what-if only
pending = db(db_pending, cid, COMPANY_V)         # saved, not refreshed yet
functions = ["All"] + sorted(co.function.dropna().unique().tolist())
func = domain_slot.selectbox("Domain", functions,
                             help="Show one function (Engineering, Sales …) under the CEO")

# ---------------------------------------------------------------- buttons
b1, b2, b3, b4, _ = st.columns([1.4, 1, 1.2, 1.7, 0.7])
b1.button("Back to all companies", icon=":material/arrow_back:", on_click=back_to_all)
b2.button("Add people", icon=":material/add:", on_click=set_flag, args=("adding", True))
b3.button("Remove people", icon=":material/remove:", on_click=set_flag,
          args=("removing", True))
b4.button("Promote / change title", icon=":material/swap_vert:", on_click=set_flag,
          args=("changing", True))
if ss.get("removing"):
    remove_people_dialog(cid, co)
if ss.get("changing"):
    change_title_dialog(cid, co)

# ---------------------------------------------------------------- headline numbers
info = co.iloc[0]
sc = scored                                      # people with a known manager (last refresh)
coverage, precision = score(apply_threshold(sc, threshold)) if len(sc) else (None, None)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Employees", len(co) - len(not_here),
          help="Leadership team members listed on the company's website" if REAL else
          "People listed for this company, after removing duplicate rows")
c2.metric("Industry", info.industry)
c3.metric("Coverage", pct(coverage, 0),
          help=f"Share of {who} who got a manager at this threshold"
               + (f" ({len(sc)} people here)" if REAL else ""))
if show_truth:
    c4.metric("Precision", pct(precision),
              help=f"Of the reporting lines drawn, the share that match {answer_key}")

# ---------------------------------------------------------------- saved changes, waiting
if len(pending):
    with st.container(border=True):
        st.caption(f"Saved changes waiting for a refresh ({len(pending)}). They are in the "
                   "database; the chart is updated by \"Refresh changed companies\" in the "
                   "sidebar.")
        for r in pending.itertuples():
            st.markdown(f"- {plain(r.summary)}")
latest = {}                       # (the in-memory what-if's per-person notes: none any more)
co["change_note"] = None


# ---------------------------------------------------------------- tabs: People, Org chart, Reports
LEVELS = {1: "1 · CEO", 2: "2 · C-level", 3: "3 · VP", 4: "4 · Director", 5: "5 · Manager",
          6: "6 · Senior", 7: "7 · Individual", 8: "8 · Junior"}
CHART_BLUE = "#2E6FD8"            # one hue for counts; >= 3:1 contrast on light and dark
KIND_WORD = {"promotion": "promoted", "demotion": "demoted", "move": "moved (same level)",
             "change": "title changed"}
names_by_id = dict(zip(co.employee_id, co.full_name))
names_by_id.update({v: "Vacant · " + str(co.title[co.employee_id == v].iloc[0])
                    for v in vacant_ids})          # a vacant seat, named by its title
# Level and department as the rules read them (title + company context)
ctx = company_context(cid, COMPANY_V, co[~co.employee_id.isin(not_here)])
co["level_now"] = co.employee_id.map(ctx.level).fillna(co.level)
co["dept_now"] = co.employee_id.map(ctx.function).fillna(co.function)


def person_status(r):
    """One line for the People table: placed / not placed + reason / left / interim ..."""
    if r.employee_id in left_ids:
        to = getattr(r, "left_to", None)
        return "left" + (f" → {to}" if isinstance(to, str) and to else "")
    reason = r.reason if isinstance(r.reason, str) and r.reason else "below threshold"
    parts = ["interim (reports to CEO)" if r.employee_id in interim_ids and pd.notna(r.manager_id)
             else "placed" if pd.notna(r.manager_id)
             else "top of company" if reason == "top of company"
             else f"not placed: {org_chart.plain_reason(reason)}"]
    if r.employee_id in latest:
        ch = latest[r.employee_id][0]
        parts.append(KIND_WORD.get(ch["kind"], "title changed")
                     + ("" if ch["old_function"] == ch["new_function"]
                        else f", now in {ch['new_function']}"))
    tag = getattr(r, "tag", None)
    if isinstance(tag, str):
        parts.append(tag)
    was = getattr(r, "was_under", None)
    if isinstance(was, str):
        why = getattr(r, "was_under_why", None)
        parts.append(f"{why if isinstance(why, str) else 'manager left'} (was under {was})")
    return " · ".join(parts)


everyone = co[~co.employee_id.isin(vacant_ids)].reset_index(drop=True)   # people, not seats
people_table = pd.DataFrame({
    "Name": everyone.full_name, "Title": everyone.title.fillna("(no title)"),
    "Level": everyone.level_now.map(LEVELS), "Department": everyone.dept_now,
    "Manager": everyone.manager_id.map(names_by_id),
    "Confidence": everyone.confidence.where(everyone.manager_id.notna()
                                            & ~everyone.employee_id.isin(left_ids)),
    "Status": [person_status(r) for r in everyone.itertuples()]})
ss.setdefault("people_version", 0)
people_key = f"people_{cid}_{ss.people_version}"


def show_in_chart():
    """A cell was clicked in the People table: if it was a name, open the Org chart tab
    at that person (like the company names on the front page)."""
    cells = ss[people_key]["selection"]["cells"]
    if cells and cells[0][1] == "Name":
        ss.highlight = ss.people_ids[cells[0][0]]
        ss.company_tab = "Org chart"
    ss.people_version += 1         # fresh table next time, so the old click doesn't stick


ss.people_ids = everyone.employee_id.tolist()       # row number -> person, for show_in_chart
tab_people, tab_chart, tab_reports = st.tabs(["People", "Org chart", "Reports"],
                                             default="Org chart", key="company_tab",
                                             on_change="rerun")   # so code can switch tabs

with tab_people:
    st.caption("Everyone in this company. Click a name to see that person in the Org chart "
               "tab. Search with the magnifying glass (top right of the table), sort by "
               "clicking a column header.")
    st.dataframe(people_table, key=people_key, on_select=show_in_chart,
                 selection_mode="single-cell", hide_index=True, width="stretch", height=560,
                 placeholder="—",
                 column_config={"Confidence": st.column_config.NumberColumn(format="%.2f")})

with tab_chart:
    view = co if func == "All" else co[(co.function == func) | (co.level == 1)]
    st.subheader(f"{plain(info.company_name)} — {func}")
    pick = ss.get("highlight")
    pick = pick if pick in set(view.employee_id) else None
    if pick:
        h1, h2 = st.columns([4, 1])
        h1.caption(f"Highlighted: **{plain(names_by_id[pick])}** (picked in the People tab)")
        h2.button("Clear highlight", on_click=set_flag, args=("highlight", None))
    # Our own HTML: names and titles are escaped inside org_chart.py before display
    st.iframe(chart_html(cid, COMPANY_V, threshold, show_truth, func, pick, view), height=720)
    last = STATE["last"]                          # newest saved change (any company)
    u1, u2 = st.columns([1.2, 4])
    u1.button("Undo last change", icon=":material/undo:", on_click=undo_last_change,
              disabled=not last,
              help=f"Undo: {plain(last['summary'])}" if last else "Nothing to undo")
    if last:
        u2.caption(f"Last saved change: {plain(last['summary'])}")


def counts(frame, gone=frozenset(), interim=frozenset(), vacant=frozenset()):
    """Headline counts of one version of the chart (before or now)."""
    people = frame[~frame.employee_id.isin(set(gone) | set(vacant))]
    top = people.reason == "top of company"
    return {"People": len(people), "Lines drawn": int(people.manager_id.notna().sum()),
            "Not placed": int((people.manager_id.isna() & ~top).sum()),
            "Interim under the CEO": int(people.employee_id.isin(interim).sum()),
            "Vacant positions": len(vacant)}


with tab_reports:
    now = co[~co.employee_id.isin(not_here)]
    st.markdown("**People by level**")
    st.html(levels_html(cid, COMPANY_V, threshold, now))
    st.markdown("**People by department**")
    per_dept = now.dept_now.fillna("unknown").value_counts()
    st.bar_chart(per_dept.rename_axis("Department").reset_index(name="People"),
                 x="Department", y="People", color=CHART_BLUE, horizontal=True,
                 sort="-People", height=max(300, 28 * len(per_dept) + 70))  # every label
    st.markdown("**Reporting lines**")
    n_now = counts(co, left_ids, interim_ids, vacant_ids)
    k1, k2, k3 = st.columns(3)
    k1.metric("Lines drawn", f"{n_now['Lines drawn']:,}")
    k2.metric("Not placed", f"{n_now['Not placed']:,}",
              help="These people do have a manager; the model wasn't confident enough to "
                   "say who, so it drew no line.")
    k3.metric("Top of company", int((now.reason == "top of company").sum()))
    gaps = now[now.manager_id.isna() & (now.reason != "top of company")]
    if len(gaps):
        why = gaps.reason.map(org_chart.plain_reason)
        top_why = why.value_counts().head(5)
        st.markdown("Top reasons for \"not placed\"")
        st.dataframe(pd.DataFrame({"Reason": top_why.index, "People": top_why.values,
                                   "Share": top_why.values / len(gaps)}),
                     hide_index=True, width="stretch",
                     column_config={"Share": st.column_config.ProgressColumn(
                         format="percent", min_value=0, max_value=1)})
    if show_truth and len(sc):            # nothing to check in a new organisation
        st.markdown("**Accuracy** (vs. " + answer_key + ")")
        a1, a2, a3 = st.columns(3)
        a1.metric("Precision", pct(precision))
        a2.metric("Coverage", pct(coverage, 0))
        a3.metric("People checked", len(sc),
                  help=f"{who.capitalize()}; changes you made here are never counted")

    # ------------------------------------------------ Before vs Now (last two refreshes)
    hist = db(db_history, cid, COMPANY_V)
    run_ids = sorted(hist.run_id.unique())
    if len(run_ids) == 2:
        before = apply_threshold(hist[hist.run_id == run_ids[0]], threshold)
        after = apply_threshold(hist[hist.run_id == run_ids[1]], threshold)
        when_ = {r: f"{t:%d %b %H:%M}" for r, t in zip(hist.run_id, hist.started_at)}
        st.markdown(f"**Before vs Now**: previous refresh (run {run_ids[0]}, "
                    f"{when_[run_ids[0]]}) vs latest refresh (run {run_ids[1]}, "
                    f"{when_[run_ids[1]]}), from the database")
        both = before[["employee_id", "manager_id"]].merge(
            after[["employee_id", "manager_id"]], on="employee_id", how="outer",
            suffixes=("_before", "_now"), indicator=True)

        def who_(x):
            return "—" if pd.isna(x) else names_by_id.get(x, x)

        rows = []
        for r in both.itertuples():
            if r._4 == "left_only":
                rows.append(("not on the chart now", who_(r.employee_id),
                             f"was under {who_(r.manager_id_before)}"))
            elif r._4 == "right_only":
                rows.append(("new on the chart", who_(r.employee_id),
                             f"now under {who_(r.manager_id_now)}"))
            elif not (pd.isna(r.manager_id_before) and pd.isna(r.manager_id_now))                     and r.manager_id_before != r.manager_id_now:
                rows.append(("manager changed", who_(r.employee_id),
                             f"{who_(r.manager_id_before)} → {who_(r.manager_id_now)}"))
        if rows:
            st.dataframe(pd.DataFrame(rows, columns=["Change", "Person", "Details"]),
                         hide_index=True, width="stretch")
        else:
            st.caption("No reporting line changed between these two refreshes.")
        n_before, n_after = counts(before), counts(after)
        keep = ["People", "Lines drawn", "Not placed"]
        lines = [(k, str(n_before[k]), str(n_after[k]), f"{n_after[k] - n_before[k]:+d}")
                 for k in keep]
        if show_truth:
            def acc(frame):
                f = frame[frame.true_manager_id.notna()]
                return score(f) if len(f) else (None, None)
            (cov0, prec0), (cov1, prec1) = acc(before), acc(after)
            lines += [("Coverage", pct(cov0, 0), pct(cov1, 0), ""),
                      ("Precision", pct(prec0), pct(prec1), "")]
        st.dataframe(pd.DataFrame(lines, columns=["", "Before", "Now", "Change"]),
                     hide_index=True, width="stretch")
    elif len(run_ids) == 1:
        st.caption("Before vs Now appears after this company's next refresh (it compares the "
                   "previous refresh with the latest one).")

# ---------------------------------------------------------------- sources (real companies)
if REAL or UK:
    st.markdown("##### Sources")
    if UK:
        st.caption(OGL)                               # the licence requires this credit
    for s in db(load_sources)[lambda s: s.company_id == cid].itertuples():
        if UK:
            st.markdown(f"- [Organogram file on data.gov.uk]({s.url}) — {s.what_it_shows}. "
                        f"Downloaded {when(s.date_checked)}. "
                        f"Licence: {s.licence}.")
        elif s.what_it_shows.startswith("Confirms "):     # answer-key source
            st.markdown(f"- [Answer-key source]({s.url}) — {s.what_it_shows}. Checked "
                        f"{when(s.date_checked)}.")
        else:
            st.markdown(f"- [{s.company_name}: leadership page]({s.url}) — "
                        f"{s.what_it_shows}. Checked {when(s.date_checked)}.")

# ---------------------------------------------------------------- lookups and gaps (Org chart tab)
with tab_chart:
    st.subheader("Who does this person report to?")
    person = st.selectbox("Person", co.full_name + " — " + co.title.fillna("(no title)"))
    row = co[(co.full_name + " — " + co.title.fillna("(no title)")) == person].iloc[0]
    chain, cur = [], row
    while pd.notna(cur.manager_id) and len(chain) < 10:
        edge_conf = cur.confidence                     # confidence of THIS reporting line
        cur = co[co.employee_id == cur.manager_id].iloc[0]
        chain.append(f"{plain(cur.full_name)} ({plain(cur.title)}) — confidence {edge_conf:.2f}")
    if chain:
        st.write("Reporting chain, bottom to top:")
        for i, c in enumerate(chain, 1):
            st.write(f"{i}. {c}")
    else:
        st.write("Top of company." if row.reason == "top of company" else
                 "No line drawn: the model wasn't confident enough to say who. Reason: "
                 + org_chart.plain_reason(row.reason))
