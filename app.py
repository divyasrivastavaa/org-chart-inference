"""
app.py  -  Step 7
Streamlit app: pick a company, see its inferred org chart.
Run locally with:  streamlit run app.py
"""
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Org Chart Inference", layout="wide")


@st.cache_data
def load():
    # On a fresh server (e.g. Streamlit Cloud) the CSVs don't exist yet: build them once.
    # Also rebuild if they were made by an older version of the code (no "scenario" column).
    stale = (not Path("data/predictions.csv").exists()
             or "scenario" not in pd.read_csv("data/truth.csv", nrows=1).columns
             or "is_contractor" not in pd.read_csv("data/predictions.csv", nrows=1).columns)
    if stale:
        import generate_data, infer
        generate_data.main()
        infer.main()
    pred = pd.read_csv("data/predictions.csv")
    truth = pd.read_csv("data/truth.csv")[["employee_id", "true_manager_id"]]
    return pred.merge(truth, on="employee_id", how="left")


df = load()
st.title("Org Chart Inference")
st.caption("Reporting lines inferred from job titles only. "
           "No line is drawn when confidence is below the threshold — "
           "a wrong edge is worse than no edge.")

# ---------------------------------------------------------------- sidebar controls
companies = df[["company_id", "company_name"]].drop_duplicates().sort_values("company_name")
label = st.sidebar.selectbox("Company", companies.company_name + " (" + companies.company_id + ")")
cid = label[-5:-1]
threshold = st.sidebar.slider("Confidence threshold", 0.5, 1.0, 0.75, 0.05)
co = df[df.company_id == cid].copy()
co["manager_id"] = co.pred_manager_id.where(co.confidence >= threshold)
below = co.pred_manager_id.notna() & co.manager_id.isna()
co.loc[below, "reason"] = "confidence below threshold"

functions = ["All"] + sorted(co.function.dropna().unique().tolist())
func = st.sidebar.selectbox("Function", functions)
show_truth = st.sidebar.checkbox("Show accuracy vs. answer key", value=True)

# ---------------------------------------------------------------- headline numbers
info = co.iloc[0]
has_true = co.true_manager_id.notna()
given = co.manager_id.notna()
right = (co.manager_id == co.true_manager_id) & given
c1, c2, c3, c4 = st.columns(4)
c1.metric("Employees", len(co))
c2.metric("Industry", info.industry)
c3.metric("Coverage", f"{given[has_true].mean():.0%}")
if show_truth:
    c4.metric("Precision", f"{right.sum() / max(given.sum(), 1):.1%}")

# ---------------------------------------------------------------- the org chart
view = co if func == "All" else co[(co.function == func) | (co.level == 1)]
name = dict(zip(co.employee_id, co.full_name))


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
