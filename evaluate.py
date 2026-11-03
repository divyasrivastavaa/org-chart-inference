"""
evaluate.py  -  Step 6
Compares data/predictions.csv with the hidden answer key data/truth.csv.
"""
import pandas as pd

truth = pd.read_csv("data/truth.csv")[["employee_id", "true_manager_id", "true_level"]]
pred = pd.read_csv("data/predictions.csv")
df = truth.merge(pred[["employee_id", "pred_manager_id", "confidence", "manager_id"]],
                 on="employee_id", how="left")      # people we dropped count as "no answer"
df = df[df.true_manager_id.notna()]                 # CEOs have no manager to find
df["correct"] = df.pred_manager_id == df.true_manager_id

# Headline numbers at the chosen threshold
given = df.manager_id.notna()
precision = (df.manager_id == df.true_manager_id)[given].mean()
coverage = given.mean()
print(f"Precision: {precision:.1%}  (of the edges we output, share that are right)")
print(f"Coverage:  {coverage:.1%}  (share of people who got a manager)")
print(f"Wrong edges: {((df.manager_id != df.true_manager_id) & given).sum()} "
      f"out of {given.sum()}")

# Threshold sweep: how precision and coverage trade off
sweep = []
for t in [0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9]:
    out = df.pred_manager_id.notna() & (df.confidence >= t)
    sweep.append(dict(threshold=t, precision=round(df.correct[out].mean(), 4),
                      coverage=round(out.mean(), 4)))
sweep = pd.DataFrame(sweep)
print("\n", sweep.to_string(index=False))
sweep.to_csv("data/threshold_sweep.csv", index=False)

# Is confidence honest? Accuracy inside each confidence band
df["band"] = pd.cut(df.confidence, [0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
print("\n", df[df.pred_manager_id.notna()].groupby("band", observed=True)
      .correct.agg(["mean", "count"]).round(3).to_string())

# Where do we fail? Accuracy by the person's true level
print("\n", df.groupby("true_level").apply(
    lambda g: pd.Series({"precision": (g.manager_id == g.true_manager_id)[g.manager_id.notna()].mean(),
                         "coverage": g.manager_id.notna().mean()}), include_groups=False)
      .round(3).to_string())
