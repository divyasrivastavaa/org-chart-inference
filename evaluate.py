"""
evaluate.py  -  Step 6
Compares predictions.csv with the hidden answer key truth.csv.
Usage: python evaluate.py [data_dir]      (default: data)
"""
import sys
from pathlib import Path

import pandas as pd

DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
truth = pd.read_csv(DATA / "truth.csv")
for col, default in [("in_dataset", True), ("scenario", "normal"),
                     ("dotted_manager_id", None)]:          # older truth files lack these
    if col not in truth:
        truth[col] = default
boss_of = dict(zip(truth.employee_id, truth.true_manager_id))   # full answer key, incl. missing

truth = truth[truth.in_dataset.astype(bool)]           # can't score people we never saw
pred = pd.read_csv(DATA / "predictions.csv")
if "rule_confidence" not in pred:                   # older predictions files
    pred["rule_confidence"] = pred.confidence
df = truth[["employee_id", "true_manager_id", "true_level", "scenario", "dotted_manager_id"]] \
    .merge(pred[["employee_id", "pred_manager_id", "confidence", "rule_confidence",
                 "manager_id"]],
           on="employee_id", how="left")      # people we dropped count as "no answer"
df = df[df.true_manager_id.notna()]                 # CEOs have no manager to find
df["correct"] = df.pred_manager_id == df.true_manager_id

# Headline numbers at the chosen threshold
given = df.manager_id.notna()
wrong = given & (df.manager_id != df.true_manager_id)
precision = (df.manager_id == df.true_manager_id)[given].mean()
coverage = given.mean()
print(f"Precision: {precision:.1%}  (of the edges we output, share that are right)")
print(f"Coverage:  {coverage:.1%}  (share of people who got a manager)")
print(f"Wrong edges: {wrong.sum()} out of {given.sum()}")

# Brier score: mean (confidence - outcome)^2 over proposed edges. 0 = perfect, lower = better.
e = df[df.pred_manager_id.notna()]
brier = ((e.confidence - e.correct) ** 2).mean()
brier_rules = ((e.rule_confidence - e.correct) ** 2).mean()
print(f"Brier score: {brier:.4f}  (hand-set rule confidence: {brier_rules:.4f})")
rules_out = e.rule_confidence >= 0.75
print(f"For comparison, hand-set rules at 0.75: precision {e.correct[rules_out].mean():.1%}, "
      f"coverage {rules_out.sum() / len(df):.1%}, wrong edges {(~e.correct[rules_out]).sum()}")


# What kind of wrong? A skip-level edge (my boss's boss) is far less harmful than a
# line to the wrong part of the company.
def is_ancestor(person, candidate):
    cur = boss_of.get(person)
    for _ in range(10):
        cur = boss_of.get(cur)
        if cur is None or pd.isna(cur):
            return False
        if cur == candidate:
            return True
    return False


skip = df[wrong].apply(lambda r: is_ancestor(r.employee_id, r.manager_id), axis=1)
dotted = (df.manager_id == df.dotted_manager_id)[wrong]
print(f"  of which skip-level (a true boss further up): {skip.sum()}, "
      f"dotted-line manager: {dotted.sum()}, "
      f"other: {wrong.sum() - skip.sum() - dotted.sum()}")

# Threshold sweep: how precision and coverage trade off
sweep = []
for t in [0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9]:
    out = df.pred_manager_id.notna() & (df.confidence >= t)
    sweep.append(dict(threshold=t, precision=round(df.correct[out].mean(), 4),
                      coverage=round(out.mean(), 4)))
sweep = pd.DataFrame(sweep)
print("\n", sweep.to_string(index=False))
sweep.to_csv(DATA / "threshold_sweep.csv", index=False)

# Is confidence honest? Accuracy inside each confidence band
df["band"] = pd.cut(df.confidence, [0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
print("\n", df[df.pred_manager_id.notna()].groupby("band", observed=True)
      .correct.agg(["mean", "count"]).round(3).to_string())


def pr(g):
    return pd.Series({"precision": (g.manager_id == g.true_manager_id)[g.manager_id.notna()].mean(),
                      "coverage": g.manager_id.notna().mean(), "people": len(g)})


# Where do we fail? Accuracy by the person's true level, and by hard-case scenario
print("\n", df.groupby("true_level").apply(pr, include_groups=False).round(3).to_string())
if df.scenario.nunique() > 1:
    print("\n", df.groupby("scenario").apply(pr, include_groups=False).round(3).to_string())
