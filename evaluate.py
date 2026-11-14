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


def score_hand_made(truth, pred):
    """Small hand-made answer key (e.g. data/real): score ONLY people with a confirmed
    manager, split by evidence_type, and list every title the model read wrong."""
    p = pred.set_index("employee_id")
    # "level" is read from the title alone; f_level is the level after company-wide
    # corrections (e.g. divisional CEO -> 2), saved when a manager was proposed
    p["level"] = p.f_level.where(p.f_level.notna(), p.level)
    # Manually added people are never scored, even if they somehow had an answer
    if "source" in truth:
        n_added = (truth.source == "manually added").sum()
        truth = truth[truth.source != "manually added"]
        if n_added:
            print(f"Not scored: {n_added} manually added people")
    df = truth[truth.true_manager_id.notna()].merge(
        p.reset_index()[["employee_id", "level", "function", "pred_manager_id", "confidence",
                         "manager_id", "reason"]], on="employee_id", how="left")

    def row(g, label):
        given = g.manager_id.notna()
        right = given & (g.manager_id == g.true_manager_id)
        prec = f"{right.sum() / given.sum():.0%}" if given.any() else "n/a (no lines drawn)"
        return {"evidence": label, "people": len(g), "lines drawn": int(given.sum()),
                "right": int(right.sum()), "wrong": int((given & ~right).sum()),
                "precision": prec, "coverage": f"{given.mean():.0%}"}

    rows = [row(df, "all")] + [row(g, k) for k, g in df.groupby("evidence_type")]
    print(f"Scored {len(df)} people with a confirmed manager "
          f"(of {len(truth)} in the file; the rest have no source and are not scored)\n")
    print(pd.DataFrame(rows).to_string(index=False))
    hidden = df.pred_manager_id.notna() & df.manager_id.isna()     # guessed, not drawn
    print(f"\nBelow-threshold guesses (not drawn): {hidden.sum()}, "
          f"of which right: {(hidden & (df.pred_manager_id == df.true_manager_id)).sum()}")

    # Titles read wrong. What the model needs to draw each confirmed line:
    #  - the title is parsed (a level AND a function),
    #  - a company CEO is read as level 1 (top of company),
    #  - someone who reports to the CEO is read as level 2 (C-level),
    #  - any other manager is read at a higher level (smaller number) than their report.
    ceo_rx = r"chief executive officer|\bceo\b"
    managers = set(df.true_manager_id)
    is_ceo = {m: bool(pd.Series(p.at[m, "title"]).str.lower().str.contains(ceo_rx).iloc[0])
              and m not in set(df.employee_id) for m in managers}
    problems = []
    for eid in sorted(set(df.employee_id) | managers):
        r = p.loc[eid]
        lvl = None if pd.isna(r.level) else int(r.level)
        why = []
        if lvl is None or pd.isna(r.function):
            why.append("could not be parsed (no function)" if lvl else "could not be parsed")
        if is_ceo.get(eid) and lvl != 1:
            why.append(f"CEO read as level {lvl}, should be 1 (top)")
        boss = df.loc[df.employee_id == eid, "true_manager_id"]
        if len(boss):
            b = boss.iloc[0]
            b_lvl = p.at[b, "level"]
            if is_ceo.get(b) and lvl != 2:
                why.append(f"reports to the CEO but read as level {lvl}, should be 2")
            elif not is_ceo.get(b) and not (pd.notna(b_lvl) and lvl and b_lvl < lvl):
                why.append(f"read at level {lvl}, not below their manager (level {b_lvl:.0f})")
        if why:
            problems.append({"employee_id": eid, "title": r.title, "level read": lvl,
                             "function read": r.function, "problem": "; ".join(why)})
    print(f"\nTitles read wrong: {len(problems)} of {len(set(df.employee_id) | managers)} "
          f"titles involved in confirmed lines (people + their managers)")
    with pd.option_context("display.width", 250, "display.max_colwidth", 70):
        print(pd.DataFrame(problems).to_string(index=False))


if "evidence_type" in truth:                       # hand-made answer key, not the generator's
    score_hand_made(truth, pd.read_csv(DATA / "predictions.csv"))
    sys.exit()

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
