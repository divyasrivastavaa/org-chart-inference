"""
app.py  -  Step 7
Streamlit app. Front page: overall results + a searchable, sortable table of all companies.
Click a row (or use the sidebar) to open that company's inferred org chart.
Run locally with:  streamlit run app.py
"""
import threading
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Org Chart Inference", layout="wide")


@st.cache_resource
def build_lock():
    return threading.Lock()        # one lock shared by every visitor's session


def is_stale():
    # Missing files, or files made by an older version of the code
    return (not Path("data/predictions.csv").exists() or not Path("data/truth.csv").exists()
            or "scenario" not in pd.read_csv("data/truth.csv", nrows=1).columns
            or "rule_confidence" not in pd.read_csv("data/predictions.csv", nrows=1).columns)


@st.cache_data
def load():
    # On a fresh server (e.g. Streamlit Cloud) the CSVs don't exist yet: build them once.
    # The lock stops two visitors from rebuilding at the same time; the second one waits,
    # then finds fresh files and skips the rebuild.
    with build_lock():
        if is_stale():
            import generate_data, infer
            generate_data.main()
            infer.main()
    pred = pd.read_csv("data/predictions.csv")
    truth = pd.read_csv("data/truth.csv")
    # Scoring frame, built exactly like evaluate.py so the numbers match the README:
    # one row per real person who has a manager to find, predictions joined on.
    scored = truth[truth.in_dataset.astype(bool) & truth.true_manager_id.notna()][
        ["company_id", "employee_id", "true_manager_id"]].merge(
        pred[["employee_id", "pred_manager_id", "confidence"]], on="employee_id", how="left")
    truth = truth[["employee_id", "true_manager_id"]]
    return pred.merge(truth, on="employee_id", how="left"), scored


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


df, scored = load()
ss = st.session_state
# Fixed order: the table's selected row number is looked up in this list
companies = (df[["company_id", "company_name", "industry"]].drop_duplicates("company_id")
             .sort_values("company_name").reset_index(drop=True))
company_label = dict(zip(companies.company_id,
                         companies.company_name + " (" + companies.company_id + ")"))

# ---------------------------------------------------------------- which company is open?
# One source of truth: ss.company_select (also the sidebar dropdown's value).
# Table clicks and the Back button write to it; the URL (?company=C001) mirrors it.
ss.setdefault("table_version", 0)
if "company_select" not in ss:
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
st.caption("Reporting lines inferred from job titles only. "
           "No line is drawn when confidence is below the threshold — "
           "a wrong edge is worse than no edge.")

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
              help="Share of people (excluding CEOs) who got a manager")
    if show_truth:
        c4.metric("Precision (all companies)", f"{precision:.1%}",
                  help="Of the reporting lines drawn, the share that match the answer key")

    g = s.groupby("company_id")
    table = companies.set_index("company_id").join(pd.DataFrame({
        "employees": df.groupby("company_id").size(),
        "coverage": 100 * g.given.mean(),
        "precision": 100 * g.right.sum() / g.given.sum().clip(lower=1),
    })).reset_index(drop=True)
    columns = ["company_name", "industry", "employees", "coverage"] + \
        (["precision"] if show_truth else [])
    st.dataframe(
        table, key=table_key, on_select="rerun", selection_mode="single-row",
        hide_index=True, width="stretch", height=560, column_order=columns,
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
co = apply_threshold(df[df.company_id == cid], threshold)
functions = ["All"] + sorted(co.function.dropna().unique().tolist())
func = st.sidebar.selectbox("Function", functions)

# ---------------------------------------------------------------- headline numbers
info = co.iloc[0]
coverage, precision = score(apply_threshold(scored[scored.company_id == cid], threshold))
c1, c2, c3, c4 = st.columns(4)
c1.metric("Employees", len(co))
c2.metric("Industry", info.industry)
c3.metric("Coverage", f"{coverage:.0%}")
if show_truth:
    c4.metric("Precision", f"{precision:.1%}")

# ---------------------------------------------------------------- the org chart
view = co if func == "All" else co[(co.function == func) | (co.level == 1)]


def node_label(r):
    title = r.title if isinstance(r.title, str) else "(no title)"
    return f"{r.full_name}\\n{title[:40]}"


def colour(r):
    if not show_truth or pd.isna(r.manager_id):
        return "white"
    return "#d9f2e3" if r.manager_id == r.true_manager_id else "#f8d7d7"


managers_shown = set(view.manager_id.dropna())
if func == "All" and len(view) > 120:
    st.info("Large company: showing leadership (Manager and above) only. "
            "Pick a function in the sidebar to see everyone in it.")
    view = view[(view.level <= 5) | view.employee_id.isin(managers_shown)]

ids = set(view.employee_id)
lines = ['digraph G {', 'rankdir=TB; node [shape=box, style="rounded,filled", '
         'fontname=Helvetica, fontsize=10];']
for r in view.itertuples():
    lines.append(f'"{r.employee_id}" [label="{node_label(r)}", fillcolor="{colour(r)}"];')
for r in view[view.manager_id.notna()].itertuples():
    if r.manager_id in ids:
        lines.append(f'"{r.manager_id}" -> "{r.employee_id}" '
                     f'[label="{r.confidence:.2f}", fontsize=8];')
lines.append("}")
st.subheader(f"{info.company_name} — {func}")
if show_truth:
    st.caption("Green = matches the answer key, red = wrong. Edge labels show confidence.")
st.graphviz_chart("\n".join(lines), width="stretch")

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
