"""Compare our rules with the Gemini judge on a fixed sample, and write
docs/judge_comparison.md.

Sample (fixed seeds, so every run uses the same people), 143 people in all, small enough
for one day of a free-tier Flash-Lite model:
  - 50 people from Benchmark seed 7 (held out)        data/holdout_seed7
  - 50 people from the UK test half                   data/uk  (U01 U04 U05 U06 U08 U12)
  - every ASX/NZX person with an answer (43)          data/real
Only people who have a manager in the answer key are sampled (precision/coverage as in
evaluate.py). Both judges use threshold 0.7: a line is drawn only at confidence >= 0.7.

Gemini answers are cached in data/gemini_cache.json, so a rerun (or a run that stopped at
a rate limit) doesn't call the API again for the same question. Between calls it waits
--delay seconds (default 7: about 8 requests a minute) to stay inside free-tier limits.
143 questions: about 17 minutes the first time, seconds after that. If the model's daily
limit runs out, rerun the next day: it continues from the cache.

Needs GEMINI_API_KEY in .streamlit/secrets.toml (never printed). Run from the repo root:
    python scripts/compare_judges.py --daily-limit "N requests per day" [--delay 7]
(--daily-limit: the model's free-tier daily limit as shown in Google AI Studio, written into
the results file.)
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import judge_gemini  # noqa: E402

THRESHOLD = 0.7
UK_TEST = ["U01", "U04", "U05", "U06", "U08", "U12"]
CACHE = ROOT / "data" / "gemini_cache.json"
OUT = ROOT / "docs" / "judge_comparison.md"


def sample():
    """[(dataset label, predictions of the sampled companies, sampled truth rows)]."""
    sets = []
    for label, folder, n, companies in [
            ("Benchmark seed 7 (held out)", "data/holdout_seed7", 50, None),
            ("UK test half", "data/uk", 50, UK_TEST),
            ("ASX/NZX (all with an answer)", "data/real", None, None)]:
        d = ROOT / folder
        if not (d / "predictions.csv").exists():
            sys.exit(f"Missing {folder}/predictions.csv - build it first (see README, How to run).")
        pred = pd.read_csv(d / "predictions.csv", low_memory=False)
        truth = pd.read_csv(d / "truth.csv", low_memory=False)
        keep = truth.true_manager_id.notna()
        if "in_dataset" in truth:
            keep &= truth.in_dataset.astype(bool)
        if "source" in truth:
            keep &= truth.source != "manually added"
        truth = truth[keep & truth.employee_id.isin(pred.employee_id)]
        if companies:
            truth = truth[truth.company_id.isin(companies)]
        if n:
            truth = truth.sample(n=n, random_state=0)
        sets.append((label, pred[pred.company_id.isin(truth.company_id)], truth))
    return sets


def score(rows):
    given = rows.manager.notna()
    right = given & (rows.manager == rows.true_manager_id)
    return {"people": len(rows), "lines": int(given.sum()), "right": int(right.sum()),
            "precision": right.sum() / given.sum() if given.any() else None,
            "coverage": given.mean()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=7.0, help="seconds between API calls")
    ap.add_argument("--daily-limit", default="not recorded",
                    help='free-tier daily limit of the model, e.g. "1,000 requests per day"')
    args = ap.parse_args()
    key, model = judge_gemini.settings()
    if not key:
        sys.exit("No Gemini key: put GEMINI_API_KEY in .streamlit/secrets.toml first.")
    judge = judge_gemini.GeminiJudge(key, model, cache_path=CACHE, delay=args.delay)
    print(f"Model: {model}")

    results, ceilings = [], []
    for label, pred, truth in sample():
        print(f"{label}: {len(truth)} people")
        rules = truth.merge(pred[["employee_id", "manager_id"]], on="employee_id", how="left")
        rules["manager"] = rules.manager_id
        rows, has_true = [], 0
        for cid, people in truth.groupby("company_id"):
            co = judge_gemini.with_context(pred[pred.company_id == cid])
            for t in people.itertuples():
                p = pred[pred.employee_id == t.employee_id].iloc[0]
                rules_pick = p.pred_manager_id if isinstance(p.pred_manager_id, str) else None
                cands = judge_gemini.candidates(co, t.employee_id, rules_pick)
                has_true += t.true_manager_id in set(cands.employee_id)
                me = co[co.employee_id == t.employee_id].iloc[0]
                try:
                    a = judge.ask(p.company_name, me, cands)
                except judge_gemini.JudgeError as e:
                    sys.exit(f"Stopped: {e}. Answers so far are cached in {CACHE.name}; "
                             "run again later to continue.")
                rows.append({"employee_id": t.employee_id, "true_manager_id": t.true_manager_id,
                             "manager": a["manager_id"] if a["confidence"] >= THRESHOLD else None})
                if len(rows) % 25 == 0:
                    print(f"  {len(rows)} done ({judge.calls} API calls so far)")
        gem = pd.DataFrame(rows)
        results += [(label, "Rules", score(rules)), (label, "Gemini", score(gem))]
        ceilings.append((label, has_true / len(truth)))

    def pct(x):
        return "–" if x is None else f"{x:.1%}"
    lines = [
        "# Rules vs Gemini judge", "",
        f"- **Model:** `{model}`, Gemini API free tier.",
        f"- **Daily free-tier limit for this model and key:** {args.daily_limit}.",
        f"- **Run:** {date.today():%d %b %Y} by `scripts/compare_judges.py`, threshold "
        f"{THRESHOLD} for both judges.", "",
        "Gemini picks from the short list of candidate "
        "managers our rules find (at most 8: the rules' own pick, more senior people in the "
        "same function or unit, the top of the organisation), or answers null.", "",
        "| Dataset | Judge | People | Lines drawn | Right | Precision | Coverage |",
        "|---|---|---|---|---|---|---|"]
    lines += [f"| {d} | {j} | {s['people']} | {s['lines']} | {s['right']} | "
              f"{pct(s['precision'])} | {pct(s['coverage'])} |" for d, j, s in results]
    lines += ["", "**Ceiling for Gemini:** how often the true manager is in the candidate list "
              "it is given (it can't do better than this):", ""]
    lines += [f"- {d}: {pct(c)}" for d, c in ceilings]
    lines += ["", f"Gemini API calls in this run: {judge.calls} (the rest came from the cache, "
              f"`data/{CACHE.name}`)."]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nSaved {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
