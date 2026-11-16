# Org chart inference — project notes for Claude Code

Take-home task for Firmable (B2B sales intelligence). Deadline: Sat 7 Nov 2026.

## The brief (summary)
- For every company, infer who reports to whom from a flat list of people + job titles,
  with a confidence on each reporting line.
- **A wrong edge is worse than no edge.** Abstain when the manager can't be resolved.
- No dataset is given: we generate one. >= 500 companies, >= 100 employees each, realistic
  mix of titles / functions / seniority / industries / sizes, and messy data.
  "Think about how you'll know if your inference is right" -> the generator keeps an answer key.
- Firmable is AI-native: how the work is built (dev loop) matters as much as the result.
- Deliverables: hosted app (pick a company, see its org chart) + public repo with code,
  generator and a short write-up (approach, key decisions, how it was measured).

## Pipeline
1. `generate_data.py` -> `data/truth.csv` (answer key) + `data/employees.csv` (messy, no manager)
2. `infer.py`        -> `data/predictions.csv` (manager_id, confidence, reason, f_* features)
3. `evaluate.py`     -> precision / coverage / wrong edges, Brier, sweep, calibration, scenarios
4. `app.py`          -> Streamlit app (builds the data itself if `data/` is missing or stale)
5. `train_confidence.py` -> trains the edge-confidence tree on a separate seed-1 dataset,
   saves `models/confidence_tree.json` (used by infer.py) + `.txt` (readable rules)

`data/` is git-ignored; everything is reproducible (app data seed 42, held-out seed 7,
training seed 1). `models/` IS committed (the hosted app needs it).

## Commands (Windows, from the repo root)
- Activate env: `.venv\Scripts\activate`
- Full loop: `python generate_data.py`, `python infer.py`, `python evaluate.py`
- Held out: `python generate_data.py --seed 7 --out data/holdout_seed7`,
  `python infer.py data/holdout_seed7`, `python evaluate.py data/holdout_seed7`
- Retrain confidence: `python train_confidence.py` (`python infer.py --rules` = no tree)
- App: `streamlit run app.py`

## Current baseline, see docs/experiments.md (E5)
Decision tree confidence, threshold 0.7 (wrong edge ~2.3x a missing edge):
- v2 data (default, seed 42): precision 95.2%, coverage 62.7%, 4,727 wrong edges, Brier 0.0433.
- v2 held-out (`--seed 7 --out data/holdout_seed7`): 95.4% / 63.7%, Brier 0.0420.
- Real data (`data/real`, 43 confirmed lines): 36 of 36 right, coverage 84%.
- UK organograms (`data/uk`, E6): test half 85.0% / 25.3%, tune half 83.6% / 12.6%
  (tune = U02 U03 U07 U09 U10 U11; never tune on the test half).
  Rebuild: `python scripts/build_uk.py`, `python infer.py data/uk`, `python evaluate.py data/uk`.
- Weakest: shared-manager teams (59% precision), manager missing from the data.
Any change must be reported against this baseline, on all four datasets,
and logged in docs/experiments.md.

## Gemini judge
`judge_gemini.py` (optional second judge). Key: st.secrets["GEMINI_API_KEY"] in
`.streamlit/secrets.toml` (git-ignored; NEVER print, log or commit it). Model: GEMINI_MODEL
secret or `gemini-3.5-flash-lite`. Comparison: `python scripts/compare_judges.py` (cached in
data/gemini_cache.json) -> docs/judge_comparison.md.

## Rules for working in this repo
- The user is new to Git/terminals: explain each change, then give exact git commands.
- Never change code without saying what changed and why.
- Evaluate on held-out companies when anything is learned or tuned (split by company_id).
- Keep the app light: Streamlit Cloud free tier has ~1 GB RAM. No torch in requirements.txt.
- Research notes: `docs/research_models.md`.
