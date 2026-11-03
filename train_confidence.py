"""
train_confidence.py  -  learn the confidence of each reporting line (replaces hand-set penalties)

1. Generate a SEPARATE training world (seed 1) in data/train_seed1/. The app's data (seed 42)
   and the held-out test data (seed 7) are never used for training, so no leakage.
2. Run the rules on it; every proposed edge becomes one row: features -> correct (1) / wrong (0).
3. Split by COMPANY (80% train / 20% validation): people in one company share a structure,
   so a random row split would leak.
4. Fit a small decision tree; check on validation that its probabilities are honest.
5. Save the tree as plain JSON (models/confidence_tree.json) + readable rules (.txt).

Decision threshold is not tuned: it comes from a stated cost. If a wrong edge is COST times
worse than a missing edge, drawing an edge with P(correct) = p is worth it when
p - COST * (1 - p) > 0, i.e. p > COST / (COST + 1).

Usage: python train_confidence.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeClassifier

import generate_data
import infer

TRAIN_DIR = Path("data/train_seed1")
MODEL_DIR = Path("models")
COST_OF_WRONG_EDGE = 4          # "a wrong edge is worse than no edge": 4x worse
MAX_DEPTH = 4                   # small enough to read; see the depth table printed below
MIN_LEAF = 200                  # every leaf's probability rests on >= 200 real edges


def calibration(p, y):
    bands = pd.cut(p, [0, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0], include_lowest=True)
    return pd.DataFrame({"predicted": p, "actual": y, "band": bands}).groupby(
        "band", observed=True).agg(mean_predicted=("predicted", "mean"),
                                   actual_correct=("actual", "mean"),
                                   edges=("actual", "size")).round(3)


def readable_rules(tree, prob):
    """Like sklearn's export_text, but every leaf shows its probability and size."""
    t, lines = tree.tree_, []

    def walk(node, depth):
        pad = "|   " * depth
        if t.children_left[node] == -1:
            verdict = "draw" if prob[node] >= COST_OF_WRONG_EDGE / (COST_OF_WRONG_EDGE + 1) \
                else "abstain"
            lines.append(f"{pad}--> P(correct) = {prob[node]:.3f}  "
                         f"({t.n_node_samples[node]} edges)  {verdict}")
            return
        name, thr = infer.FEATURES[t.feature[node]], t.threshold[node]
        lines.append(f"{pad}{name} <= {thr:.1f}")
        walk(t.children_left[node], depth + 1)
        lines.append(f"{pad}{name} >  {thr:.1f}")
        walk(t.children_right[node], depth + 1)

    walk(0, 0)
    return "\n".join(lines) + "\n"


def main():
    generate_data.main(version=2, seed=1, out=TRAIN_DIR)
    infer.main(TRAIN_DIR, use_model=False)              # rules only: features + labels
    pred = pd.read_csv(TRAIN_DIR / "predictions.csv")
    truth = pd.read_csv(TRAIN_DIR / "truth.csv")[["employee_id", "true_manager_id"]]
    e = pred[pred.pred_manager_id.notna()].merge(truth, on="employee_id")
    e["y"] = (e.pred_manager_id == e.true_manager_id).astype(int)
    X_all = e[infer.FEATURES].astype(float).fillna(-1)

    companies = np.array(sorted(e.company_id.unique()))
    rng = np.random.default_rng(0)
    val_cos = set(rng.choice(companies, size=len(companies) // 5, replace=False))
    is_val = e.company_id.isin(val_cos).values
    Xtr, ytr, Xva, yva = X_all[~is_val], e.y[~is_val], X_all[is_val], e.y[is_val]
    print(f"\nTraining edges: {len(Xtr)} ({len(companies) - len(val_cos)} companies), "
          f"validation edges: {len(Xva)} ({len(val_cos)} companies), "
          f"share correct: {e.y.mean():.1%}")

    print("\nValidation Brier score by tree depth (lower is better):")
    rule_brier = ((e.rule_confidence[is_val] - yva) ** 2).mean()
    print(f"  hand-set rules: {rule_brier:.4f}")
    for depth in [2, 3, 4, 5, 6, 8]:
        t = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=MIN_LEAF, random_state=0)
        p = t.fit(Xtr, ytr).predict_proba(Xva)[:, 1]
        print(f"  depth {depth}: {((p - yva) ** 2).mean():.4f}  ({t.get_n_leaves()} leaves)")

    tree = DecisionTreeClassifier(max_depth=MAX_DEPTH, min_samples_leaf=MIN_LEAF,
                                  random_state=0).fit(Xtr, ytr)
    p = tree.predict_proba(Xva)[:, 1]
    threshold = COST_OF_WRONG_EDGE / (COST_OF_WRONG_EDGE + 1)
    keep = p >= threshold
    print(f"\nChosen depth {MAX_DEPTH}, threshold {threshold} "
          f"(a wrong edge costs {COST_OF_WRONG_EDGE}x a missing one)")
    print(f"Validation: precision {yva[keep].mean():.1%} on {keep.sum()} of {len(p)} edges "
          f"kept; rules at 0.75 keep {(e.rule_confidence[is_val] >= 0.75).sum()} "
          f"at {yva[(e.rule_confidence[is_val] >= 0.75).values].mean():.1%}")
    print("\nIs the tree's confidence honest? (validation companies)")
    print(calibration(p, yva.values).to_string())

    importance = pd.Series(tree.feature_importances_, infer.FEATURES).sort_values(
        ascending=False)
    print("\nFeature importance:\n" + importance[importance > 0].round(3).to_string())

    # Save as plain JSON: the app walks the tree itself, no scikit-learn needed there
    t = tree.tree_
    values = t.value[:, 0, :]
    prob = (values[:, list(tree.classes_).index(1)] / values.sum(axis=1)).round(4)
    MODEL_DIR.mkdir(exist_ok=True)
    model = dict(features=infer.FEATURES, decision_threshold=threshold,
                 cost_of_wrong_edge=COST_OF_WRONG_EDGE, max_depth=MAX_DEPTH,
                 trained_on="v2 seed 1, 400 companies (by company split)",
                 children_left=t.children_left.tolist(),
                 children_right=t.children_right.tolist(),
                 feature=t.feature.tolist(), threshold=t.threshold.round(4).tolist(),
                 prob=prob.tolist())
    (MODEL_DIR / "confidence_tree.json").write_text(json.dumps(model, indent=1))
    (MODEL_DIR / "confidence_tree.txt").write_text(
        "Decision tree for edge confidence. Each leaf: P(edge is correct), training edges.\n"
        f"Edges below {threshold} are not drawn.\n\n" + readable_rules(tree, prob))
    # Sanity check: our JSON walker must agree with scikit-learn
    mine = infer.tree_predict(model, Xva.values)
    assert np.allclose(mine, p, atol=1e-3), "JSON tree disagrees with scikit-learn"
    print(f"\nSaved {MODEL_DIR / 'confidence_tree.json'} and confidence_tree.txt")


if __name__ == "__main__":
    main()
