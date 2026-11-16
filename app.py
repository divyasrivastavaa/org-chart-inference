"""
app.py  -  Step 7
Streamlit app. Front page: one searchable, sortable table of all companies from both
datasets ("Benchmark": 500 generated, "ASX/NZX leadership": 20 real), with buttons to
show one or both datasets, and an "Add people" button.
Click a company name (or use the sidebar) to open that company's inferred org chart.
Company page: a "What if" section (someone leaves, add people) that changes the chart for
this visitor only, in memory. Nothing is ever saved to files.
Run locally with:  streamlit run app.py
"""
import copy
import json
import runpy
import threading
from pathlib import Path

import pandas as pd
import streamlit as st

import judge_gemini
import org_chart
import whatif

st.set_page_config(page_title="Org Chart Inference", layout="wide")
ROOT = Path(__file__).resolve().parent
# Internal key -> (name shown in the app, folder). Names say where the data comes from.
DATASETS = {"synthetic": ("Benchmark", "data"),
            "real": ("ASX/NZX leadership", "data/real"),
            "uk": ("UK government organograms", "data/uk")}
GEN, ASX, UKN = (DATASETS[k][0] for k in ("synthetic", "real", "uk"))
OGL = ("Contains public sector information licensed under the "
       "[Open Government Licence v3.0]"
       "(https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).")
MODEL_THRESHOLD = json.loads((ROOT / "models" / "confidence_tree.json").read_text())[
    "decision_threshold"]


@st.cache_resource
def build_lock():
    return threading.Lock()        # one lock shared by every visitor's session


def is_stale(folder):
    # Missing files, or files made by an older version of the code
    pred, truth = Path(folder, "predictions.csv"), Path(folder, "truth.csv")
    if not pred.exists() or not truth.exists():
        return True
    needed = {"data": "scenario", "data/uk": "true_level"}.get(folder, "evidence_type")
    if folder != "data":            # data/real/sources.csv: one row per source (new format)
        sources = Path(DATASETS["real"][1], "sources.csv")
        src = pd.read_csv(sources) if sources.exists() else pd.DataFrame()
        if "licence" not in src:
            return True
        mine = (src.what_it_shows.str.startswith("Confirms ") if folder == "data/real"
                else src.dataset == UKN)          # answer-key rows / UK rows present?
        if not mine.any():
            return True
    first = pd.read_csv(pred, nrows=1)
    return (needed not in pd.read_csv(truth, nrows=1).columns
            or "rule_confidence" not in first.columns
            or first.get("threshold", pd.Series([None])).iloc[0] != MODEL_THRESHOLD)


def build(folder):
    """Build a dataset from code in this repo (no internet needed)."""
    import infer
    if folder == "data":
        import generate_data
        generate_data.main()
    elif folder == "data/uk":      # from the raw files committed in data/raw/uk/ (offline)
        runpy.run_path(str(ROOT / "scripts" / "build_uk.py"), run_name="__main__")
    else:                          # the real leadership lists are typed into build_real.py
        runpy.run_path(str(ROOT / "scripts" / "build_real.py"), run_name="__main__")
        runpy.run_path(str(ROOT / "scripts" / "build_real_truth.py"), run_name="__main__")
    infer.main(folder)


@st.cache_data
def load(folder):
    # On a fresh server (e.g. Streamlit Cloud) the CSVs don't exist yet: build them once.
    # The lock stops two visitors from rebuilding at the same time; the second one waits,
    # then finds fresh files and skips the rebuild.
    with build_lock():
        if is_stale(folder):
            build(folder)
    emp = pd.read_csv(Path(folder, "employees.csv"), low_memory=False)   # for what-if
    pred = pd.read_csv(Path(folder, "predictions.csv"), low_memory=False)
    truth = pd.read_csv(Path(folder, "truth.csv"), low_memory=False)
    # Scoring frame, built exactly like evaluate.py so the numbers match the README:
    # one row per person who has a known manager, predictions joined on.
    keep = truth.true_manager_id.notna()
    if "in_dataset" in truth:                 # synthetic: people we never saw can't be scored
        keep &= truth.in_dataset.astype(bool)
    if "vacant" in truth:                     # leavers.csv: empty positions aren't scored
        keep &= truth.vacant.astype(str) != "True"
    if "source" in truth:                     # real: people added by hand are never scored
        keep &= truth.source != "manually added"
    scored = truth[keep][
        ["company_id", "employee_id", "true_manager_id"]].merge(
        pred[["employee_id", "pred_manager_id", "confidence"]], on="employee_id", how="left")
    # Can a drawn line be coloured right/wrong? Synthetic: everyone. Real: only people
    # with a manager confirmed by a written source.
    truth["checkable"] = keep if folder != "data" else True
    truth = truth[["employee_id", "true_manager_id", "checkable"]]
    return pred.merge(truth, on="employee_id", how="left"), scored, emp, truth


def load_sources():
    """data/real/sources.csv: one row per source, written by scripts/build_real.py,
    build_real_truth.py and build_uk.py. Read fresh every time (47 rows): a cached copy
    outlived rebuilds of the file and hid the UK rows."""
    path = Path(DATASETS["real"][1], "sources.csv")
    return pd.read_csv(path) if path.exists() else pd.DataFrame(
        columns=["dataset", "company_id", "company_name", "url", "what_it_shows",
                 "date_checked", "licence"])


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
ss.setdefault("history", [])       # earlier versions of ss.whatif, for "Undo last change"
ss.setdefault("datasets", list(DATASETS))
ss.datasets = ss.datasets          # keep the dataset buttons' choice while a company is open

# ---------------------------------------------------------------- both datasets
# Company IDs never clash (synthetic C001..., real R01...), so one table holds both.
loaded = {k: load(folder) for k, (_, folder) in DATASETS.items()}
# Fixed order: the table's clicked row number is looked up in this list
companies = (pd.concat([d[0][["company_id", "company_name", "industry"]]
                        .drop_duplicates("company_id").assign(dataset=k)
                        for k, d in loaded.items()])
             .sort_values("company_name").reset_index(drop=True))
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


@st.dialog("How it works", width="large")
def docs_dialog():
    # The same picture as in the README (docs/flow.svg), so the two never drift apart
    svg = (ROOT / "docs" / "flow.svg").read_text(encoding="utf-8")
    st.image(svg.replace("</defs>", FLOW_ANIMATION + "\n  </defs>"), width="stretch")
    st.caption("Numbers are from the latest evaluation at threshold 0.7 "
               "(see docs/experiments.md, E5 and E6).")
    src = load_sources()
    key = src.what_it_shows.str.startswith("Confirms ")       # answer-key sources
    pages = src[(src.dataset == ASX) & ~key]
    uk = src[src.dataset == UKN]
    st.markdown("#### Sources")
    st.markdown(
        f"- **{GEN}:** generated by `generate_data.py`. No outside source: every company, "
        "name and title is made up, and the generator writes the answer key itself.\n"
        f"- **{UKN}:** the senior-staff organogram CSV of {len(uk)} organisations from "
        f"data.gov.uk (downloaded {when(uk.date_checked)}; "
        "raw files in `data/raw/uk/`, links in `data/real/sources.csv`). The published "
        "\"reports to\" column is the answer key. " + OGL + "\n"
        f"- **{ASX}:** each company's own leadership page ({len(pages)} pages, checked "
        f"{when(pages.date_checked)}). Its small answer key "
        f"(43 confirmed lines) comes from {key.sum()} written company sources: "
        "announcements about the person, and annual reports or governance statements. "
        "All of them are listed in `data/real/sources.csv` and under the chart on each "
        "company's page; the exact quotes are in `scripts/build_real_truth.py`.")


def set_flag(name, value):
    ss[name] = value


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
        emp_to = loaded[ds][2]
        taken = set(emp_to.full_name[emp_to.company_id == to])
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


# ---------------------------------------------------------------- sidebar
st.sidebar.selectbox("Company", [""] + list(company_label), key="company_select",
                     format_func=lambda c: company_label.get(c, "All companies"))
threshold = st.sidebar.slider("Confidence threshold", 0.5, 1.0, MODEL_THRESHOLD, 0.05,
                              help="Confidence comes from a decision tree trained on a "
                                   "separate dataset. Default 0.7: a wrong line is treated "
                                   "as costing about 2.3 times a missing one, so a line is "
                                   "drawn when it is right at least 70% of the time.")
show_truth = st.sidebar.checkbox("Show accuracy vs. answer key", value=True,
                                 help="Off: hides precision numbers and green colouring.")
domain_slot = st.sidebar.container()      # the company page puts its "Domain" filter here
st.sidebar.divider()
if st.sidebar.button("Docs: how it works", icon=":material/account_tree:", width="stretch"):
    docs_dialog()
if ss.get("adding"):
    add_people_dialog()
cid = ss.company_select
if cid:
    st.query_params["company"] = cid
else:
    st.query_params.pop("company", None)

st.title("Org Chart Inference")
REAL = bool(cid) and dataset_of[cid] == "real"
UK = bool(cid) and dataset_of[cid] == "uk" and cid not in NEW_ORGS
if cid in NEW_ORGS:
    st.caption("**New organisation**, created by you in this browser session. Everyone here "
               "was added by hand, so there is no answer key. No line is drawn when "
               "confidence is below the threshold — a wrong line is worse than no line.")
elif REAL:
    st.caption(f"**Dataset: {ASX}.** The leadership teams of 20 ASX/NZX-listed companies, "
               "from each company's own website (14 Nov 2026). Reporting lines are inferred "
               "from job titles only. Accuracy is checked only for the 43 people whose "
               "manager the company has confirmed in writing.")
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
               f"{GEN}: 500 generated companies with a hidden answer key. {UKN}: 12 real "
               "UK government organisations, whose published reporting lines are the answer "
               f"key. {ASX}: 20 real leadership teams, where only the 43 managers confirmed "
               "in writing serve as an answer key. No line is drawn when confidence is below "
               "the threshold — a wrong line is worse than no line.")
who = ("people with a confirmed manager" if REAL
       else "people (excluding the top post)" if UK else "people (excluding CEOs)")
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
                        format_func=lambda k: DATASETS[k][0], label_visibility="collapsed")

    chosen = ss.datasets           # the pills below; read now so the metrics come first
    if not chosen:
        pills()
        st.info("Pick at least one dataset above.")
        st.stop()
    checked_who = {"synthetic": f"{GEN}: everyone except CEOs",
                   "uk": f"{UKN}: everyone except the top post",
                   "real": f"{ASX}: only people with a confirmed manager"}
    key_of = {"synthetic": f"the answer key ({GEN})",
              "uk": f"the published reporting lines ({UKN})",
              "real": f"the companies' written statements ({ASX})"}
    who = "people who can be checked (" + "; ".join(checked_who[k] for k in chosen) + ")"
    answer_key = " or ".join(key_of[k] for k in chosen)
    s = apply_threshold(pd.concat([loaded[k][1] for k in chosen]), threshold)
    coverage, precision = score(s)
    shown = companies[companies.dataset.isin(chosen)].reset_index(drop=True)
    ss.table_ids = shown.company_id.tolist()      # row number -> company, for open_clicked
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Companies", f"{len(shown):,}")
    c2.metric("People", f"{sum(len(loaded[k][0]) for k in chosen):,}")
    c3.metric("Coverage", f"{coverage:.1%}",
              help=f"For the datasets selected below: share of {who} who got a manager "
                   f"({len(s):,} people can be checked)")
    if show_truth:
        c4.metric("Precision", f"{precision:.1%}",
                  help=f"For the datasets selected below: of the reporting lines drawn, the "
                       f"share that match {answer_key}")
    pills()

    g = s.groupby("company_id")
    every = pd.concat([loaded[k][0] for k in chosen])      # everyone, scored or not
    drawn = every.pred_manager_id.notna() & (every.confidence >= threshold)
    table = shown.set_index("company_id").join(pd.DataFrame({
        "employees": every.groupby("company_id").size(),
        "lines": drawn.groupby(every.company_id).sum(),
        "coverage": 100 * g.given.mean(),
        "precision": 100 * g.right.sum() / g.given.sum().where(g.given.sum() > 0),
    })).reset_index(drop=True)
    table["dataset"] = table.dataset.map(lambda k: DATASETS[k][0])
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
df, scored, emp, truth_small = loaded[dataset_of[cid]]    # this company's dataset

# What-if changes (this visitor only): re-run inference in memory for affected companies.
# Only changes made in this company's dataset (people can't move between datasets).
events = {k: [e for e in ss.whatif[k] if dataset_of.get(e["company_id"]) == dataset_of[cid]]
          for k in ("leavers", "added")}
if cid in whatif.touched(emp, events):
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
functions = ["All"] + sorted(co.function.dropna().unique().tolist())
func = domain_slot.selectbox("Domain", functions,
                             help="Show one function (Engineering, Sales …) under the CEO")

# ---------------------------------------------------------------- buttons
b1, b2, b3, _ = st.columns([1.4, 1, 1.2, 2.4])
b1.button("Back to all companies", icon=":material/arrow_back:", on_click=back_to_all)
b2.button("Add people", icon=":material/add:", on_click=set_flag, args=("adding", True))
b3.button("Remove people", icon=":material/remove:", on_click=set_flag,
          args=("removing", True))
if ss.get("removing"):
    remove_people_dialog(cid, co, not_here)

# ---------------------------------------------------------------- headline numbers
info = co.iloc[0]
sc = scored[(scored.company_id == cid) & ~scored.employee_id.isin(not_here | interim_ids)
            & ~scored.true_manager_id.isin(left_ids)][
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

# ---------------------------------------------------------------- the org chart
view = co if func == "All" else co[(co.function == func) | (co.level == 1)]
st.subheader(f"{info.company_name} — {func}")
# Our own HTML: names and titles are escaped inside org_chart.py before display
st.iframe(org_chart.render_html(view, show_truth), height=720)


def chart_csv(co):
    """The chart as it is on screen now (this threshold, these changes), one row each."""
    def status(r):
        if r.employee_id in left_ids:
            return "left" + (f" → {r.left_to}" if isinstance(r.left_to, str) and r.left_to
                             else "")
        parts = [f"manager left, was under {r.was_under}"] \
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
    }).to_csv(index=False).encode("utf-8-sig")   # with BOM: Excel then reads "→" correctly


u1, u2, u3, _ = st.columns([1.2, 1.2, 1.1, 2.5])
u1.button("Undo last change", icon=":material/undo:", on_click=undo,
          disabled=not ss.history)
u2.button("Reset to original", icon=":material/restart_alt:", on_click=reset_all,
          disabled=not ss.history)
u3.download_button("Download CSV", chart_csv(co), file_name=f"org_chart_{cid}.csv",
                   mime="text/csv", icon=":material/download:", on_click="ignore")

# ---------------------------------------------------------------- sources (real companies)
if REAL or UK:
    st.markdown("##### Sources")
    for s in load_sources()[lambda s: s.company_id == cid].itertuples():
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

# ---------------------------------------------------------------- lookups and gaps
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
