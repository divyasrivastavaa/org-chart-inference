"""
app.py  -  Step 7 + Phase 2: database
Streamlit app. Everything it shows is read from the Postgres database (db/read.py). The
CSV files are only used once, to import the source data (scripts/load_db.py).
Front page: one searchable, sortable table of all companies from all datasets
("Benchmark": generated, "ASX/NZX leadership" and "UK government organograms": real,
"PDL people": real people from People Data Labs), with dataset buttons and "Add people".
Click a company name (or use the sidebar) to open that company's inferred org chart.
Company page: a "What if" section (someone leaves, add people, promote) that changes the
chart for this visitor only, in memory. Nothing is saved.
Sidebar "Database": job history, runs & cost, read-only SQL.
Run locally with:  streamlit run app.py
"""
import copy
import hmac
import json
import re
from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st

import judge_gemini
import org_chart
import whatif
from db import read as dbread
from db.connect import get_secret

st.set_page_config(page_title="Org Chart Inference", layout="wide")
ROOT = Path(__file__).resolve().parent
# Internal key -> name shown in the app. Names say where the data comes from.
DATASETS = {"synthetic": "Benchmark", "real": "ASX/NZX leadership",
            "uk": "UK government organograms", "pdl": "PDL people (real)"}
GEN, ASX, UKN, PDL = (DATASETS[k] for k in ("synthetic", "real", "uk", "pdl"))
OGL = ("Contains public sector information licensed under the "
       "[Open Government Licence v3.0]"
       "(https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).")
MODEL_THRESHOLD = json.loads((ROOT / "models" / "confidence_tree.json").read_text())[
    "decision_threshold"]
UK_TUNE = {"U02", "U03", "U07", "U09", "U10", "U11"}   # UK fixes were designed on these
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


def show_pdl_names():
    """True once this visitor entered the right DB_VIEW_PIN."""
    return bool(st.session_state.get("pdl_ok"))


@st.cache_data(ttl=TTL, show_spinner=False)
def db_companies():
    return dbread.companies()


@st.cache_data(ttl=TTL, show_spinner=False)
def db_stats(threshold):
    return dbread.company_stats(threshold)


@st.cache_data(ttl=TTL, show_spinner=False)
def db_counts():
    return dbread.counts()


@st.cache_data(ttl=TTL, show_spinner=False)
def db_accuracy(threshold, company_ids):
    return dbread.accuracy(threshold, list(company_ids))


@st.cache_data(ttl=TTL, show_spinner=False)
def db_company(company_id, show_pdl):
    return dbread.company(company_id, show_pdl)


@st.cache_data(ttl=TTL, show_spinner=False)
def db_employees(company_ids, show_pdl):
    return dbread.employees(company_ids, show_pdl)


@st.cache_data(ttl=TTL, show_spinner=False)
def load_sources():
    """Where each real company / organisation comes from (table sources)."""
    return dbread.sources()


@st.cache_data(ttl=TTL, show_spinner=False)
def db_runs():
    return dbread.runs()


@st.cache_resource
def gemini_judge(api_key, model):
    """One Gemini judge per key/model for the whole server; it remembers its answers in
    memory, so the reruns of a page don't call the API again."""
    return judge_gemini.GeminiJudge(api_key, model)


def current_judge():
    """The Gemini judge, or None if no key is set (or the package is missing)."""
    key, model = judge_gemini.settings()
    if not key:
        return None
    try:
        return gemini_judge(key, model)
    except judge_gemini.JudgeError:
        return None


JUDGES = {"rules": "Rules (default)", "gemini": "Gemini"}


def judge_choice(key):
    """The "Advanced" part of the Add / Remove people popups."""
    with st.expander("Advanced: which judge places these people"):
        choice = st.radio("Judge", list(JUDGES), format_func=JUDGES.get, key=key,
                          horizontal=True, label_visibility="collapsed")
        if choice == "gemini" and not judge_gemini.settings()[0]:
            st.caption("No Gemini API key is set (GEMINI_API_KEY in Streamlit secrets), so "
                       "the rules will place them.")
        else:
            st.caption("Gemini chooses from the short list of candidate managers our rules "
                       "find. Lines below the confidence threshold are not drawn.")
    return choice


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
ss.setdefault("whatif", whatif.empty())
ss.whatif.setdefault("new_orgs", [])
ss.whatif.setdefault("changes", [])
ss.setdefault("history", [])       # earlier versions of ss.whatif, for "Undo last change"
ss.setdefault("datasets", list(DATASETS))
ss.setdefault("pdl_ok", False)
ss.datasets = ss.datasets          # keep the dataset buttons' choice while a company is open

# ---------------------------------------------------------------- all datasets
# Company IDs never clash (C001, R01, U01, P-...), so one table holds all of them.
try:
    companies = db_companies()
except (psycopg.Error, RuntimeError):            # no secrets, wrong password, paused project
    st.title("Org Chart Inference")
    st.error(NOT_CONNECTED)
    st.stop()
companies = (companies[["company_id", "company_name", "industry", "dataset", "employees"]]
             .sort_values("company_name").reset_index(drop=True))
COUNTS = db(db_counts)
company_label = dict(zip(companies.company_id,
                         companies.company_name + " (" + companies.company_id + ")"))
dataset_of = dict(zip(companies.company_id, companies.dataset))
for o in ss.whatif["new_orgs"]:    # organisations this visitor created with "Add people"
    company_label[o["company_id"]] = f"{o['company_name']} ({o['company_id']}, new)"
    dataset_of[o["company_id"]] = o["dataset"]
NEW_ORGS = {o["company_id"] for o in ss.whatif["new_orgs"]}
st.query_params.pop("data", None)  # old links (?data=real&company=R01) still open R01

# ---------------------------------------------------------------- which company is open?
# One source of truth: ss.company_select (also the sidebar dropdown's value).
# Table clicks and the Back button write to it; the URL (?company=C001) mirrors it.
if ss.get("company_select") not in [""] + list(company_label):
    from_url = st.query_params.get("company", "")
    ss.company_select = from_url if from_url in company_label else ""
if ss.get("open_next"):            # set by the "Add people" window: open that company
    ss.company_select = ss.pop("open_next")
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
        r">\d+ organisations, [\d,]+ posts \(data\.gov\.uk\)<":
            f">{COUNTS['uk']['companies']} organisations, {COUNTS['uk']['people']:,} posts "
            "(data.gov.uk)<",
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
        "with their job history, from the People Data Labs person API (cached; no answer "
        "key). Their names are shown only with the PIN.")


def set_flag(name, value):
    ss[name] = value


def database_page():
    """Sidebar → Database: job history, runs & cost, and a read-only SQL box."""
    st.button("Back", icon=":material/arrow_back:", on_click=set_flag, args=("db_view", False))
    st.subheader("Database")
    show = show_pdl_names()
    t_jobs, t_runs, t_sql = st.tabs(["Job history", "Runs & cost", "SQL"])

    with t_jobs:
        text = st.text_input("Find a person by name or id",
                             placeholder="e.g. C001-E0002, or part of a name")
        st.caption("Every job a person has had, oldest first. A change never overwrites "
                   "anything: the old job is closed and a new one opened. "
                   + ("" if show else "PDL people (real) can be found by their id only "
                      "(e.g. P-…); their names need the PIN."))
        if text.strip():
            found = db(dbread.find_people, text, show)
            if found.empty:
                st.info("No one found.")
            else:
                label = dict(zip(found.employee_id, found.full_name.fillna("(no name)")
                                 + " (" + found.employee_id + ")"))
                pid = st.selectbox(f"Person ({len(found)} found, first 50)", list(label),
                                   format_func=label.get)
                hist = db(dbread.job_history, pid)
                kinds = hist.change_type.value_counts()
                st.caption(f"{len(hist)} job(s): " + ", ".join(
                    f"{n} {k.replace('_', ' ')}" for k, n in kinds.items()))
                st.dataframe(hist, hide_index=True, width="stretch")

    with t_runs:
        runs = db(db_runs)
        cost = runs.cost_usd.astype(float)
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Runs", f"{len(runs):,}")
        r2.metric("Total cost", f"${cost.sum():,.4f}",
                  help="Gemini tokens × list price + PDL credits × price (db/costs.py). "
                       "The rules cost nothing.")
        r3.metric("PDL credits used", f"{int(runs.pdl_credits_used.sum()):,}")
        r4.metric("Gemini tokens",
                  f"{int(runs.gemini_input_tokens.sum() + runs.gemini_output_tokens.sum()):,}")
        st.caption("Newest first. A refresh re-infers only companies that changed; "
                   "'pdl_import' rows are People Data Labs API calls.")
        st.dataframe(runs, hide_index=True, width="stretch")

    with t_sql:
        pick = st.selectbox("Example queries", ["(write your own)"] + list(dbread.EXAMPLES))
        sql = st.text_area("SQL: one SELECT or WITH statement",
                           value=dbread.EXAMPLES.get(pick, "select * from companies"),
                           height=220, key=f"sql_{pick}")
        st.caption("Read only: runs as a role that can only read this project's tables, "
                   "inside a READ ONLY transaction, stopped after 5 seconds, at most 500 rows "
                   "(LIMIT 500 is added if the query has no LIMIT). "
                   + ("" if show else "PDL people's rows are hidden without the PIN."))
        if st.button("Run query", type="primary", icon=":material/play_arrow:"):
            try:
                result = dbread.run_readonly(sql, show)
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


def remember():
    """Before each change: keep a copy of the changes so far, for "Undo last change"."""
    ss.history.append(copy.deepcopy(ss.whatif))


def undo():
    ss.whatif = ss.history.pop()


def reset_all():
    ss.whatif, ss.history = whatif.empty(), []


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
    st.caption("New people are placed by the normal rules and marked \"manually added\". "
               "They have no answer key, so they never count in precision. This is only for "
               "you, in this browser session: nothing is saved.")
    judge = judge_choice("add_judge")
    if not st.button("Add and place them", type="primary"):
        return
    people = parse_people(table, text)
    if to == NEW_ORG:
        to_id, ds = f"N{len(ss.whatif['new_orgs']) + 1:02d}", "synthetic"
        taken = set()
    else:
        to_id, ds = to, dataset_of[to]
        taken = (set() if to in NEW_ORGS
                 else db(dbread.names_in, to, show_pdl_names()))
    taken |= {a["full_name"] for a in ss.whatif["added"] if a["company_id"] == to_id}
    names = [n for n, _ in people if n]
    if to == NEW_ORG and not org_name.strip():
        st.error("Please give the new organisation a name.")
    elif not people:
        st.error("Please add at least one person.")
    elif any(not d for _, d in people):
        st.error("Every person needs a designation (job title).")
    elif set(names) & taken or len(names) != len(set(names)):
        st.error("These names are already in this company: "
                 + ", ".join(sorted((set(names) & taken)
                                    | {n for n in names if names.count(n) > 1})))
    else:
        remember()
        if to == NEW_ORG:
            ss.whatif["new_orgs"].append({"company_id": to_id, "dataset": ds,
                                          "company_name": org_name.strip()})
        k = 1
        for name, desig in people:
            while not name:                           # "Name (optional)": New hire 1, 2 …
                name = f"New hire {k}" if f"New hire {k}" not in taken else ""
                k += 1
            taken.add(name)
            ss.whatif["added"].append(whatif.person(
                to_id, name, desig, company_name=org_name.strip() or None, judge=judge))
        ss.open_next = to_id       # open the company to show where they landed
        ss.adding = False
        st.rerun()


@st.dialog("Remove people", width="large", on_dismiss=lambda: set_flag("removing", False))
def remove_people_dialog(cid, co, not_here):
    people = co[~co.employee_id.isin(not_here)]
    label = dict(zip(people.employee_id,
                     people.full_name + " — " + people.title.fillna("(no title)")))
    leaving = st.multiselect("Who is leaving?", list(label), format_func=label.get,
                             placeholder="Pick one or more people")
    moving = st.text_input("Moving to (optional)", placeholder="e.g. Google")
    st.caption("Their card stays on the chart, faded. The people who reported to them are "
               "placed again by the normal rules, or wait for a new manager if no one fits. "
               "Only for you, in this browser session: nothing is saved.")
    judge = judge_choice("remove_judge")
    if st.button("Remove and re-place their team", type="primary", disabled=not leaving):
        remember()
        boss = dict(zip(co.employee_id, co.manager_id))
        for eid in leaving:
            ss.whatif["leavers"].append(whatif.leaver(
                cid, eid, people.loc[people.employee_id == eid, "full_name"].iloc[0], moving,
                boss[eid] if isinstance(boss[eid], str) else None,
                co.employee_id[co.manager_id == eid].tolist(), judge=judge))
        ss.removing = False
        st.rerun()


@st.dialog("Promote / change title", width="large",
           on_dismiss=lambda: set_flag("changing", False))
def change_title_dialog(cid, co, not_here):
    people = co[~co.employee_id.isin(not_here)]
    label = dict(zip(people.employee_id,
                     people.full_name + " — " + people.title.fillna("(no title)")))
    eid = st.selectbox("Who?", list(label), format_func=label.get, index=None,
                       placeholder="Pick a person")
    new_title = st.text_input("New job title", placeholder="e.g. VP Engineering")
    vacant = st.checkbox("Leave old position vacant", value=True,
                         help="On: a dashed vacant card keeps their old place and their team "
                              "stays under it. Off: their team stays with them if that still "
                              "fits, otherwise it is re-placed like a leaver's team.")
    st.caption("The person stays, with the new title, and is placed again by the normal "
               "rules. Never counted in precision. Only for you, in this browser session: "
               "nothing is saved.")
    if not st.button("Apply change", type="primary", disabled=not eid or not new_title.strip()):
        return
    problem = whatif.check_title(people, eid, new_title)
    if problem:
        st.warning(problem)
        return
    row = people[people.employee_id == eid].iloc[0]
    old = whatif.in_context(people).loc[eid]
    new = whatif.in_context(people, eid, new_title.strip()).loc[eid]
    team = people[people.manager_id == eid]
    names = dict(zip(co.employee_id, co.full_name))
    remember()
    ss.whatif["changes"].append(whatif.change(
        cid, eid, row.full_name, row.title, new_title, vacant,
        (old.level, old.function), (new.level, new.function), row.manager_id,
        names.get(row.manager_id), row.confidence if pd.notna(row.manager_id) else 0.0,
        dict(zip(team.employee_id, team.confidence))))
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
st.sidebar.divider()
if st.sidebar.button("Docs: how it works", icon=":material/account_tree:", width="stretch"):
    docs_dialog()

# ---------------------------------------------------------------- sidebar: Database
st.sidebar.divider()
st.sidebar.markdown("**Database**")
st.sidebar.button("Job history · Runs & cost · SQL", icon=":material/database:",
                  width="stretch", on_click=set_flag, args=("db_view", True))
PIN = get_secret("DB_VIEW_PIN")


def check_pin():
    entered = ss.get("pin_input", "")
    ss.pdl_ok = bool(PIN) and hmac.compare_digest(str(entered), str(PIN))
    ss.pin_wrong = bool(entered) and not ss.pdl_ok
    ss.pin_input = ""                           # never keep the PIN in the page


if ss.pdl_ok:
    st.sidebar.caption("PDL people's names are shown.")
    st.sidebar.button("Hide PDL names", on_click=set_flag, args=("pdl_ok", False),
                      width="stretch")
elif PIN:
    st.sidebar.text_input("PIN to show PDL names", type="password", key="pin_input",
                          on_change=check_pin,
                          help="People from People Data Labs are real people. Without the "
                               "PIN they are shown as 'PDL person #id'.")
    if ss.get("pin_wrong"):
        st.sidebar.caption(":red[Wrong PIN.]")
else:
    st.sidebar.caption("PDL people are shown as 'PDL person #id' (no DB_VIEW_PIN set).")

if ss.get("adding"):
    add_people_dialog()
cid = ss.company_select
if cid:
    st.query_params["company"] = cid
else:
    st.query_params.pop("company", None)

st.title("Org Chart Inference")
if ss.get("db_view"):
    database_page()
    st.stop()
REAL = bool(cid) and dataset_of[cid] == "real"
UK = bool(cid) and dataset_of[cid] == "uk" and cid not in NEW_ORGS
PDLCO = bool(cid) and dataset_of[cid] == "pdl"
N = {k: COUNTS.get(k, {"companies": 0, "people": 0}) for k in DATASETS}
if cid in NEW_ORGS:
    st.caption("**New organisation**, created by you in this browser session. Everyone here "
               "was added by hand, so there is no answer key. No line is drawn when "
               "confidence is below the threshold — a wrong line is worse than no line.")
elif REAL:
    st.caption(f"**Dataset: {ASX}.** The leadership teams of {N['real']['companies']} "
               "ASX/NZX-listed companies, from each company's own website (14 Nov 2026). "
               "Reporting lines are inferred from job titles only. Accuracy is checked only "
               f"for the {COUNTS['confirmed_lines']} people whose manager the company has "
               "confirmed in writing.")
elif PDLCO:
    st.caption(f"**Dataset: {PDL}.** Real people from the People Data Labs person API "
               "(senior people only, with their job history; see Database → Job history). "
               "There is no answer key, so accuracy can't be checked. Names are shown only "
               "with the PIN. No line is drawn when confidence is below the threshold — a "
               "wrong line is worse than no line.")
elif UK:
    st.caption(f"**Dataset: {UKN}.** The senior-staff organogram this organisation "
               "publishes on data.gov.uk. The model sees only names (often withheld), job "
               "titles and units; the published \"reports to\" column is the answer key and is "
               "never shown to it. No line is drawn when confidence is below the threshold — "
               "a wrong line is worse than no line. " + OGL)
elif cid:
    st.caption(f"**Dataset: {GEN}.** Made by our data generator, which keeps an answer key "
               "of every true manager. Reporting lines are inferred from job titles only. "
               "No line is drawn when confidence is below the threshold — "
               "a wrong line is worse than no line.")
else:
    st.caption("Reporting lines are inferred only from a list of names and job titles. "
               f"{GEN}: {N['synthetic']['companies']:,} generated companies with a hidden "
               f"answer key. {UKN}: {N['uk']['companies']} real UK government organisations, "
               f"whose published reporting lines are the answer key. {ASX}: "
               f"{N['real']['companies']} real leadership teams, where only the "
               f"{COUNTS['confirmed_lines']} managers confirmed in writing serve as an answer "
               f"key. {PDL}: {N['pdl']['people']} real people at {N['pdl']['companies']} "
               "company, no answer key. All read from the database. No line is drawn when "
               "confidence is below the threshold — a wrong line is worse than no line.")
who = ("people with a confirmed manager" if REAL
       else "people (excluding the top post)" if UK
       else "people with a known manager (none: no answer key)" if PDLCO
       else "people (excluding CEOs)")
answer_key = ("the companies' written statements" if REAL
              else "the published reporting lines" if UK else "the answer key")

# ================================================================ front page: all companies
if not cid:
    st.write("Click a company's name to see its org chart. Filter by dataset, search with "
             "the magnifying glass (top right of the table), sort by clicking a column header.")
    st.button("Add people", icon=":material/add:", on_click=set_flag, args=("adding", True),
              help="Add people to a company, or to a new organisation")

    def pills():
        st.subheader("Companies")
        return st.pills("Dataset", list(DATASETS), selection_mode="multi", key="datasets",
                        format_func=DATASETS.get, label_visibility="collapsed")

    chosen = ss.datasets           # the pills below; read now so the metrics come first
    if not chosen:
        pills()
        st.info("Pick at least one dataset above.")
        st.stop()
    checked_who = {"synthetic": f"{GEN}: everyone except CEOs",
                   "uk": f"{UKN}: everyone except the top post",
                   "real": f"{ASX}: only people with a confirmed manager",
                   "pdl": f"{PDL}: nobody (no answer key)"}
    key_of = {"synthetic": f"the answer key ({GEN})",
              "uk": f"the published reporting lines ({UKN})",
              "real": f"the companies' written statements ({ASX})",
              "pdl": f"no answer key ({PDL})"}
    who = "people who can be checked (" + "; ".join(checked_who[k] for k in chosen) + ")"
    answer_key = " or ".join(key_of[k] for k in chosen)
    shown = companies[companies.dataset.isin(chosen)].reset_index(drop=True)
    # Per company from the database: lines drawn, people scored, given a line, right
    table = shown.merge(db(db_stats, threshold), on="company_id", how="left")
    n_scored, n_given, n_right = (int(table[c].fillna(0).sum())
                                  for c in ("scored", "given", "right"))
    coverage = n_given / n_scored if n_scored else None
    precision = n_right / n_given if n_given else None
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Companies", f"{len(shown):,}")
    c2.metric("People", f"{int(shown.employees.sum()):,}")
    c3.metric("Coverage", pct(coverage),
              help=f"For the datasets selected below: share of {who} who got a manager "
                   f"({n_scored:,} people can be checked)")
    if show_truth:
        c4.metric("Precision", pct(precision),
                  help=f"For the datasets selected below: of the reporting lines drawn, the "
                       f"share that match {answer_key}")
    pills()

    table["lines"] = table.lines.fillna(0)
    table["coverage"] = 100 * table.given / table.scored.where(table.scored > 0)
    table["precision"] = 100 * table.right / table.given.where(table.given > 0)
    # Largest companies first (ties stay A-Z); header clicks can still re-sort
    table = table.sort_values("employees", ascending=False, kind="stable").reset_index(drop=True)
    ss.table_ids = table.company_id.tolist()      # row number -> company, for open_clicked
    table["dataset"] = table.dataset.map(DATASETS.get)
    if "real" in chosen:
        st.caption(f"{ASX}: coverage and precision only count people whose manager "
                   "the company confirmed in writing; \"None\" = no such people at that "
                   "company.")
    columns = ["company_name", "dataset", "industry", "employees", "lines", "coverage"] + \
        (["precision"] if show_truth else [])
    st.dataframe(
        table, key=table_key, on_select=open_clicked, selection_mode="single-cell",
        hide_index=True, width="stretch", height=560 if chosen != ["real"] else 740,
        column_order=columns,
        column_config={
            "company_name": st.column_config.TextColumn("Company"),
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
# This company from the database: chart rows, scored rows, answer key (empty if new)
df, scored, truth_small = db(db_company, cid, show_pdl_names())

# What-if changes (this visitor only): re-run inference in memory for affected companies.
# Only changes made in this company's dataset (people can't move between datasets).
events = {k: [e for e in ss.whatif[k] if dataset_of.get(e["company_id"]) == dataset_of[cid]]
          for k in ("leavers", "added", "changes")}
emp = None
if any(events.values()):
    # Rows needed by what-if: this company, every company with a change, and the companies
    # leavers are moving to (found by name in the same dataset)
    ids = {cid} | {e["company_id"] for k in events for e in events[k]}
    ids |= set(db(dbread.companies_named,
                  [lv["new_company"] for lv in events["leavers"] if lv["new_company"]],
                  dbread.SOURCE_OF[dataset_of[cid]]))
    emp = db(db_employees, tuple(sorted(ids - NEW_ORGS)), show_pdl_names())
if emp is not None and cid in whatif.touched(emp, events):
    wp = whatif.apply(emp, events, current_judge())
    base = wp[wp.company_id == cid].merge(truth_small, on="employee_id", how="left")
    base["checkable"] = base.checkable.fillna(False)    # added / moved-in people
else:
    base = df[df.company_id == cid]
co = apply_threshold(base, threshold)
gone = co.left.astype(str) == "True" if "left" in co else pd.Series(False, index=co.index)
left_ids = set(co.employee_id[gone])
if left_ids:
    co.loc[gone, "manager_id"] = co.loc[gone, "former_manager"]   # faded card at old place
    # Whose manager in the answer key has left: that answer no longer applies
    co.loc[co.true_manager_id.isin(left_ids), "checkable"] = False
interim_ids = set(co.employee_id[co.interim == True]) if "interim" in co else set()  # noqa
co.loc[co.employee_id.isin(interim_ids), "checkable"] = False     # interim: never scored
vacant_ids = set(co.employee_id[co.vacant.astype(str) == "True"]) if "vacant" in co else set()
not_here = vacant_ids | left_ids                 # cards without a person working here now
# Title changes here: the people changed and their old teams are never scored
my_changes = [c for c in events["changes"] if c["company_id"] == cid]
changed_people = {c["employee_id"] for c in my_changes}
changed_ids = changed_people | {e for c in my_changes for e in c["reports"]}
co.loc[co.employee_id.isin(changed_ids) | co.true_manager_id.isin(changed_people),
       "checkable"] = False
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
    remove_people_dialog(cid, co, not_here)
if ss.get("changing"):
    change_title_dialog(cid, co, not_here)

# ---------------------------------------------------------------- headline numbers
info = co.iloc[0]
sc = scored[(scored.company_id == cid)
            & ~scored.employee_id.isin(not_here | interim_ids | changed_ids)
            & ~scored.true_manager_id.isin(left_ids | changed_people)][
    ["employee_id", "true_manager_id"]].merge(
    co[["employee_id", "pred_manager_id", "confidence"]], on="employee_id", how="left")
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

# ---------------------------------------------------------------- what changed here?
name_of = dict(zip(co.employee_id, co.full_name + " (" + co.title.fillna("") + ")"))
notes = []
for lv in events["leavers"]:
    if lv["company_id"] == cid:
        team = co[co.was_under == lv["full_name"]] if "was_under" in co else co.iloc[0:0]
        interim = int((team.manager_id.notna() & team.employee_id.isin(interim_ids)).sum())
        placed = int(team.manager_id.notna().sum()) - interim
        notes.append(f"🚪 **{lv['full_name']}** left"
                     + (f" → {lv['new_company']}" if lv["new_company"] else "")
                     + f". {len(team)} direct report(s): {placed} re-placed, "
                       f"{interim} interim under the CEO, "
                       f"{len(team) - placed - interim} waiting for a new manager"
                     + (" (placed by Gemini)." if (team.get("judge") == "Gemini").any()
                        else "."))

def change_lines(i, ch):
    """What step i (a title change) did: title, level, department, manager."""
    nxt = next((c for c in my_changes[i + 1:] if c["employee_id"] == ch["employee_id"]), None)
    if nxt:                       # changed again later: this step ended where that one began
        mgr, conf, why = nxt["old_manager_name"], nxt["old_confidence"], None
    else:                         # the chart as shown now
        r = co[co.employee_id == ch["employee_id"]].iloc[0]
        mgr = (co.full_name[co.employee_id == r.manager_id].iloc[0]
               if pd.notna(r.manager_id) else None)
        conf, why = r.confidence, r.reason if isinstance(r.reason, str) and r.reason else None
    show = lambda x: "?" if x is None else x                                # noqa: E731
    same = ch["old_function"] == ch["new_function"]
    return [f"title: {show(ch['old_title'])} → {ch['new_title']}",
            f"level: {show(ch['old_level'])} → {show(ch['new_level'])} ({ch['kind']})",
            f"department: {show(ch['old_function'])} → {show(ch['new_function'])} ("
            + ("same department" if same else "moved to a different department") + ")",
            f"manager: {ch['old_manager_name'] or 'not placed'} → "
            + (f"{mgr} (confidence {conf:.2f})" if mgr
               else "not placed" + (f" ({why})" if why else ""))]


latest = {}                       # person -> their last change, for the card and the CSV
for i, ch in enumerate(my_changes):
    lines = change_lines(i, ch)
    latest[ch["employee_id"]] = (ch, lines)
    team = co[co.employee_id.isin(ch["reports"]) & ~co.employee_id.isin(left_ids)]
    if team.empty:
        about = "no direct reports"
    elif ch["vacant"]:
        about = (f"{len(team)} direct report(s) stay under the vacant "
                 f"\"{ch['old_title']}\" position")
    else:
        drawn = team.manager_id.notna()
        stayed = int((team.manager_id == ch["employee_id"]).sum())
        interim = int((drawn & team.employee_id.isin(interim_ids)).sum())
        about = (f"{len(team)} direct report(s): {stayed} stayed with them, "
                 f"{int(drawn.sum()) - stayed - interim} re-placed, {interim} interim under "
                 f"the CEO, {len(team) - int(drawn.sum())} waiting for a new manager")
    notes.append(f"🔀 **{ch['full_name']}**: {ch['kind']}  \n" + "  \n".join(lines + [about]))
co["change_note"] = co.employee_id.map(lambda e: "\n".join(latest[e][1]) if e in latest
                                       else None)
tagged = co[co.tag.notna() & ~co.employee_id.isin(not_here)] if "tag" in co else co.iloc[0:0]
for r in tagged.itertuples():
    where = (f"reports to {name_of[r.manager_id]}, confidence {r.confidence:.2f}"
             if pd.notna(r.manager_id) else f"no manager drawn — {r.reason}")
    by = " · placed by Gemini" if getattr(r, "judge", None) == "Gemini" else ""
    notes.append(f"➕ **{r.full_name}** ({r.title}, {r.tag}{by}): {where}")
# Gemini was chosen but couldn't be used: say so (rules placed them instead)
fallbacks = sorted(set(co.judge.dropna()) - {"Gemini"}) if "judge" in co else []
if notes:
    with st.container(border=True):
        st.caption("Your changes (only for you, in this browser session; nothing is saved)")
        for n in notes:
            st.markdown(n)
        for f in fallbacks:
            st.caption(f"ⓘ Gemini was chosen, but it couldn't be used, so these people were "
                       f"placed by the {f}.")

def chart_csv(co):
    """The chart as it is on screen now (this threshold, these changes), one row each."""
    def status(r):
        if r.employee_id in left_ids:
            return "left" + (f" → {r.left_to}" if isinstance(r.left_to, str) and r.left_to
                             else "")
        if r.employee_id in vacant_ids:
            note = getattr(r, "vacancy_note", None)
            return "vacant position" + (f" ({note})" if isinstance(note, str) else "")
        why = getattr(r, "was_under_why", None)
        parts = [f"{why if isinstance(why, str) else 'manager left'}, was under {r.was_under}"] \
            if isinstance(getattr(r, "was_under", None), str) else []
        if r.employee_id in interim_ids and pd.notna(r.manager_id):
            parts.insert(0, "interim, reports to CEO")
        if isinstance(getattr(r, "tag", None), str):
            parts.append(r.tag)
        return "; ".join(parts)
    here = ~co.employee_id.isin(left_ids)
    return pd.DataFrame({
        "employee_id": co.employee_id, "name": co.full_name, "title": co.title,
        "domain": co.function, "manager_id": co.manager_id,
        "manager_name": co.manager_id.map(dict(zip(co.employee_id, co.full_name))),
        "confidence": co.confidence.where(co.manager_id.notna() & here).round(3),
        "reason_no_manager": co.reason.where(co.manager_id.isna()),
        "status": [status(r) for r in co.itertuples()],
        # The person's last title change on this chart (blank if none)
        **{col: co.employee_id.map(lambda e, k=key: latest[e][0][k] if e in latest else None)
           for col, key in [("change_type", "kind"), ("old_title", "old_title"),
                            ("old_level", "old_level"), ("old_function", "old_function"),
                            ("old_manager", "old_manager_name")]},
    }).to_csv(index=False).encode("utf-8-sig")   # with BOM: Excel then reads "→" correctly


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
ctx = whatif.in_context(co[~co.employee_id.isin(not_here)])
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
             else f"not placed: {reason}"]
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
    st.subheader(f"{info.company_name} — {func}")
    pick = ss.get("highlight")
    pick = pick if pick in set(view.employee_id) else None
    if pick:
        h1, h2 = st.columns([4, 1])
        h1.caption(f"Highlighted: **{names_by_id[pick]}** (picked in the People tab)")
        h2.button("Clear highlight", on_click=set_flag, args=("highlight", None))
    # Our own HTML: names and titles are escaped inside org_chart.py before display
    st.iframe(org_chart.render_html(view, show_truth, pick), height=720)
    u1, u2, u3, _ = st.columns([1.2, 1.2, 1.1, 2.5])
    u1.button("Undo last change", icon=":material/undo:", on_click=undo,
              disabled=not ss.history)
    u2.button("Reset to original", icon=":material/restart_alt:", on_click=reset_all,
              disabled=not ss.history)
    u3.download_button("Download CSV", chart_csv(co), file_name=f"org_chart_{cid}.csv",
                       mime="text/csv", icon=":material/download:", on_click="ignore")


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
    r1, r2 = st.columns(2)
    with r1:
        st.markdown("**People per level**")
        per_level = now.level_now.map(LEVELS).fillna("unknown").value_counts()
        per_level = per_level.reindex(list(LEVELS.values()) + (   # all 8 levels, 0 if none
            ["unknown"] if "unknown" in per_level else []), fill_value=0)
        # Horizontal, so the labels fit; "1 · CEO" ... "8 · Junior" sort top to bottom
        st.bar_chart(per_level.rename_axis("Level").reset_index(name="People"), x="Level",
                     y="People", color=CHART_BLUE, horizontal=True, sort=False,
                     height=max(300, 28 * len(per_level) + 70))
    with r2:
        st.markdown("**People per department**")
        per_dept = now.dept_now.fillna("unknown").value_counts()
        st.bar_chart(per_dept.rename_axis("Department").reset_index(name="People"),
                     x="Department", y="People", color=CHART_BLUE, horizontal=True,
                     sort="-People", height=max(300, 28 * len(per_dept) + 70))  # every label
    st.markdown("**Reporting lines**")
    n_now = counts(co, left_ids, interim_ids, vacant_ids)
    k1, k2, k3 = st.columns(3)
    k1.metric("Lines drawn", f"{n_now['Lines drawn']:,}")
    k2.metric("Not placed", f"{n_now['Not placed']:,}",
              help="No manager drawn: no candidate, several candidates, or confidence below "
                   "the threshold")
    k3.metric("Top of company", int((now.reason == "top of company").sum()))
    gaps = now[now.manager_id.isna() & (now.reason != "top of company")]
    if len(gaps):
        why = gaps.reason.fillna("below threshold").replace("", "below threshold")
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

    # ------------------------------------------------ Before vs Now (what-if changes)
    rows = []
    for lv in events["leavers"]:
        if lv["company_id"] == cid:
            rows.append(("left", lv["full_name"], f"moved to {lv['new_company']}"
                         if lv["new_company"] else "left the company"))
    for r in tagged.itertuples():
        where = (f"reports to {names_by_id.get(r.manager_id)}" if pd.notna(r.manager_id)
                 else "top of company" if r.reason == "top of company" else "not placed")
        rows.append(("joined" if r.tag.startswith("joined") else "added", r.full_name,
                     f"{r.title} · {r.tag} · {where}"))
    for ch in my_changes:
        detail = f"{ch['old_title']} → {ch['new_title']}"
        if ch["old_function"] != ch["new_function"]:
            detail += f" · {ch['old_function']} → {ch['new_function']}"
        if ch["vacant"]:
            detail += " · old position left vacant"
        rows.append((KIND_WORD.get(ch["kind"], "title changed"), ch["full_name"], detail))
    if rows:
        st.markdown("**Before vs Now** (your changes, only for you in this browser session)")
        st.dataframe(pd.DataFrame(rows, columns=["Change", "Person", "Details"]),
                     hide_index=True, width="stretch")
        before = (apply_threshold(df[df.company_id == cid], threshold)
                  if cid not in NEW_ORGS else co.iloc[0:0])
        # One text row per measure, built in full first: no column changes type later
        n_before = counts(before)
        lines = [(k, str(n_before[k]), str(n_now[k]), f"{n_now[k] - n_before[k]:+d}")
                 for k in n_now]
        if show_truth:
            sc0 = scored[scored.company_id == cid]
            cov0, prec0 = score(apply_threshold(sc0, threshold)) if len(sc0) else (None, None)
            lines += [("Coverage", pct(cov0, 0), pct(coverage, 0), ""),
                      ("Precision", pct(prec0), pct(precision), "")]
        st.dataframe(pd.DataFrame(lines, columns=["", "Before", "Now", "Change"]),
                     hide_index=True, width="stretch")
        if show_truth:
            st.caption("\"Now\" precision and coverage leave out the people you changed and "
                       "their teams, so they are not exactly comparable.")

# ---------------------------------------------------------------- sources (real companies)
if REAL or UK:
    st.markdown("##### Sources")
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
        chain.append(f"{cur.full_name} ({cur.title}) — confidence {edge_conf:.2f}")
    if chain:
        st.write("Reporting chain, bottom to top:")
        for i, c in enumerate(chain, 1):
            st.write(f"{i}. {c}")
    else:
        st.write(f"No manager assigned. Reason: {row.reason or 'below threshold'}")

    st.subheader("People left without a manager")
    st.dataframe(co[co.manager_id.isna() & ~co.employee_id.isin(not_here)][
        ["full_name", "title", "department", "reason"]],
                 width="stretch", hide_index=True)
