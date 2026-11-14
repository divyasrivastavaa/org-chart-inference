"""
app.py  -  Step 7
Streamlit app. Sidebar: pick a dataset (500 synthetic companies, or 20 real companies).
Front page: overall results + a searchable, sortable table of all companies.
Click a row (or use the sidebar) to open that company's inferred org chart.
Company page: a "What if" section (someone leaves, add people) that changes the chart for
this visitor only, in memory. Nothing is ever saved to files.
Run locally with:  streamlit run app.py
"""
import runpy
import threading
from pathlib import Path

import pandas as pd
import streamlit as st

import org_chart
import whatif

st.set_page_config(page_title="Org Chart Inference", layout="wide")
ROOT = Path(__file__).resolve().parent
DATASETS = {"synthetic": ("Synthetic (500 companies)", "data"),
            "real": ("Real companies (20)", "data/real")}


@st.cache_resource
def build_lock():
    return threading.Lock()        # one lock shared by every visitor's session


def is_stale(folder):
    # Missing files, or files made by an older version of the code
    pred, truth = Path(folder, "predictions.csv"), Path(folder, "truth.csv")
    if not pred.exists() or not truth.exists():
        return True
    needed = "scenario" if folder == "data" else "evidence_type"
    return (needed not in pd.read_csv(truth, nrows=1).columns
            or "rule_confidence" not in pd.read_csv(pred, nrows=1).columns)


def build(folder):
    """Build a dataset from code in this repo (no internet needed)."""
    import infer
    if folder == "data":
        import generate_data
        generate_data.main()
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


def reset_view():
    """Called when the dataset changes: back to the front page, no what-if changes."""
    ss.company_select = ""
    ss.table_version += 1
    ss.whatif = whatif.empty()


# ---------------------------------------------------------------- which dataset?
if "dataset" not in ss:
    from_url = st.query_params.get("data", "synthetic")
    ss.dataset = from_url if from_url in DATASETS else "synthetic"
st.sidebar.radio("Dataset", list(DATASETS), key="dataset", on_change=reset_view,
                 format_func=lambda k: DATASETS[k][0])
REAL = ss.dataset == "real"
if REAL:
    st.query_params["data"] = "real"
else:
    st.query_params.pop("data", None)

df, scored, emp, truth_small = load(DATASETS[ss.dataset][1])
# Fixed order: the table's selected row number is looked up in this list
companies = (df[["company_id", "company_name", "industry"]].drop_duplicates("company_id")
             .sort_values("company_name").reset_index(drop=True))
company_label = dict(zip(companies.company_id,
                         companies.company_name + " (" + companies.company_id + ")"))

# ---------------------------------------------------------------- which company is open?
# One source of truth: ss.company_select (also the sidebar dropdown's value).
# Table clicks and the Back button write to it; the URL (?company=C001) mirrors it.
if ss.get("company_select") not in [""] + list(company_label):
    from_url = st.query_params.get("company", "")
    ss.company_select = from_url if from_url in company_label else ""
table_key = f"company_table_{ss.table_version}"
clicked = ss.get(table_key)
if clicked and clicked["selection"]["rows"]:
    ss.company_select = companies.company_id.iloc[clicked["selection"]["rows"][0]]
    ss.table_version += 1          # fresh table next time, so the old click doesn't stick


def back_to_all():
    ss.company_select = ""
    ss.table_version += 1


# ---------------------------------------------------------------- sidebar
st.sidebar.selectbox("Company", [""] + companies.company_id.tolist(), key="company_select",
                     format_func=lambda c: company_label.get(c, "All companies"))
default_t = float(df.threshold.iloc[0]) if "threshold" in df else 0.75  # set by infer.py
threshold = st.sidebar.slider("Confidence threshold", 0.5, 1.0, default_t, 0.05,
                              help="Confidence comes from a decision tree trained on a "
                                   "separate dataset. Default 0.8: a wrong edge is treated "
                                   "as 4x worse than a missing one.")
show_truth = st.sidebar.checkbox("Show accuracy vs. answer key", value=True)
cid = ss.company_select
if cid:
    st.query_params["company"] = cid
else:
    st.query_params.pop("company", None)

st.title("Org Chart Inference")
if REAL:
    st.caption("Real leadership teams of 20 ASX/NZX-listed companies, from each company's "
               "own website (14 Nov 2026). Reporting lines are inferred from job titles only. "
               "Accuracy is checked only for the 43 people whose manager the company has "
               "confirmed in writing.")
else:
    st.caption("Reporting lines inferred from job titles only. "
               "No line is drawn when confidence is below the threshold — "
               "a wrong edge is worse than no edge.")
who = "people with a confirmed manager" if REAL else "people (excluding CEOs)"
answer_key = "the companies' written statements" if REAL else "the answer key"

# ================================================================ front page: all companies
if not cid:
    st.write("Welcome! Pick a company in the table to see its inferred org chart. "
             "Search with the magnifying glass (top right of the table), sort by clicking "
             "a column header.")
    s = apply_threshold(scored, threshold)
    coverage, precision = score(s)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Companies", f"{len(companies):,}")
    c2.metric("People", f"{len(df):,}")
    c3.metric("Coverage (all companies)", f"{coverage:.1%}",
              help=f"Share of {who} who got a manager"
                   + (f" ({len(s)} people can be checked)" if REAL else ""))
    if show_truth:
        c4.metric("Precision (all companies)", f"{precision:.1%}",
                  help=f"Of the reporting lines drawn, the share that match {answer_key}")

    g = s.groupby("company_id")
    table = companies.set_index("company_id").join(pd.DataFrame({
        "employees": df.groupby("company_id").size(),
        "coverage": 100 * g.given.mean(),
        "precision": 100 * g.right.sum() / g.given.sum().where(g.given.sum() > 0),
    })).reset_index(drop=True)
    if REAL:
        st.caption("Coverage and precision only count people whose manager the company "
                   "confirmed in writing; \"None\" = no such people at that company.")
    columns = ["company_name", "industry", "employees", "coverage"] + \
        (["precision"] if show_truth else [])
    st.dataframe(
        table, key=table_key, on_select="rerun", selection_mode="single-row",
        hide_index=True, width="stretch", height=560 if not REAL else 740,
        column_order=columns,
        column_config={
            "company_name": st.column_config.TextColumn("Company"),
            "industry": st.column_config.TextColumn("Industry"),
            "employees": st.column_config.NumberColumn("Employees", format="%d"),
            "coverage": st.column_config.NumberColumn("Coverage", format="%.0f%%"),
            "precision": st.column_config.NumberColumn("Precision", format="%.1f%%"),
        })
    st.stop()

# ================================================================ company page
st.button("← Back to all companies", on_click=back_to_all)

# What-if changes (this visitor only): re-run inference in memory for affected companies
events = ss.whatif
if cid in whatif.touched(emp, events):
    wp = whatif.apply(emp, events)
    base = wp[wp.company_id == cid].merge(truth_small, on="employee_id", how="left")
    base["checkable"] = base.checkable.fillna(False)    # added / moved-in people
else:
    base = df[df.company_id == cid]
co = apply_threshold(base, threshold)
vacant_ids = set(co.employee_id[co.vacant.astype(str) == "True"]) if "vacant" in co else set()
functions = ["All"] + sorted(co.function.dropna().unique().tolist())
func = st.sidebar.selectbox("Function", functions)

# ---------------------------------------------------------------- headline numbers
info = co.iloc[0]
sc = scored[(scored.company_id == cid) & ~scored.employee_id.isin(vacant_ids)][
    ["employee_id", "true_manager_id"]].merge(
    co[["employee_id", "pred_manager_id", "confidence"]], on="employee_id", how="left")
coverage, precision = score(apply_threshold(sc, threshold)) if len(sc) else (None, None)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Employees", len(co),
          help="Leadership team members listed on the company's website" if REAL else
          "People listed for this company, after removing duplicate rows")
c2.metric("Industry", info.industry)
c3.metric("Coverage", pct(coverage, 0),
          help=f"Share of {who} who got a manager at this threshold"
               + (f" ({len(sc)} people here)" if REAL else ""))
if show_truth:
    c4.metric("Precision", pct(precision),
              help=f"Of the reporting lines drawn, the share that match {answer_key}")

# ---------------------------------------------------------------- what if?
n_changes = len(events["leavers"]) + len(events["added"])
with st.expander(f"What if… ({n_changes} change{'s' if n_changes != 1 else ''} active)"
                 if n_changes else "What if… someone leaves, or new people join",
                 expanded=bool(n_changes)):
    st.caption("Try changes on this company's chart. They are only for you, in this browser "
               "session: nothing is saved, and the results above and on the front page "
               "for all companies are not changed by them.")
    left, right = st.columns(2)
    people = co[~co.employee_id.isin(vacant_ids)]
    with left.form("leave", clear_on_submit=True):
        st.markdown("**Someone leaves**")
        who_leaves = st.selectbox("Person", people.employee_id,
                                  format_func=lambda e: " — ".join(
                                      people.loc[people.employee_id == e,
                                                 ["full_name", "title"]].fillna("").iloc[0]))
        others = [n for n in companies.company_name if n != info.company_name]
        new_co = st.selectbox("Joins (optional)", ["(a company outside this data)"] + others)
        if st.form_submit_button("Simulate leaving"):
            name = people.loc[people.employee_id == who_leaves, "full_name"].iloc[0]
            joins = "" if new_co.startswith("(") else new_co
            ss.whatif["leavers"].append(whatif.leaver(cid, name, joins))
            st.rerun()
    with right.form("add", clear_on_submit=True):
        st.markdown("**Add people**")
        new_name = st.text_input("Name")
        new_title = st.text_input("Title", placeholder="e.g. Chief Risk Officer")
        new_dept = st.text_input("Department (optional)")
        if st.form_submit_button("Add"):
            if not new_name.strip() or not new_title.strip():
                st.error("Please give a name and a title.")
            elif new_name.strip() in set(co.full_name):
                st.error(f"{new_name.strip()} is already in this company.")
            else:
                ss.whatif["added"].append(whatif.person(cid, new_name, new_title, new_dept))
                st.rerun()

    # What happened? One line per change that affects this company
    name_of = dict(zip(co.employee_id, co.full_name + " (" + co.title.fillna("") + ")"))
    for lv in events["leavers"]:
        if lv["company_id"] == cid and "former_holder" in co:
            pos = co[co.employee_id.isin(vacant_ids) & (co.former_holder == lv["full_name"])]
            if len(pos):
                n_left = (co.manager_id == pos.employee_id.iloc[0]).sum()
                st.write(f"🚪 **{lv['full_name']}** left. Their role *{pos.title.iloc[0]}* is "
                         f"now a vacant card; {n_left} direct report(s) are marked "
                         f"**manager left**.")
        elif lv["new_company"] == info.company_name:
            st.write(f"➡️ **{lv['full_name']}** joined from another company (see below).")
    added_here = co[co.full_name.isin([a["full_name"] for a in events["added"]
                                       if a["company_id"] == cid])]
    moved_in = co[co.full_name.isin([lv["full_name"] for lv in events["leavers"]
                                     if lv["new_company"] == info.company_name])
                  & ~co.employee_id.isin(vacant_ids)]
    for r in pd.concat([added_here, moved_in]).drop_duplicates("employee_id").itertuples():
        if pd.notna(r.manager_id):
            where = f"reports to {name_of[r.manager_id]}, confidence {r.confidence:.2f}"
        else:
            where = f"no manager drawn — {r.reason}"
        st.write(f"➕ **{r.full_name}** ({r.title}): {where}")
    if n_changes and st.button("Reset all what-if changes"):
        ss.whatif = whatif.empty()
        st.rerun()

# ---------------------------------------------------------------- the org chart
view = co if func == "All" else co[(co.function == func) | (co.level == 1)]
st.subheader(f"{info.company_name} — {func}")
# Our own HTML: names and titles are escaped inside org_chart.py before display
st.iframe(org_chart.render_html(view, show_truth), height=720)

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
st.dataframe(co[co.manager_id.isna()][["full_name", "title", "department", "reason"]],
             width="stretch", hide_index=True)
