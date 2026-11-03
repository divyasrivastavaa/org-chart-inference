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
2. `infer.py`        -> `data/predictions.csv` (manager_id, confidence, reason)
3. `evaluate.py`     -> precision / coverage / wrong edges, threshold sweep, calibration, by level
4. `app.py`          -> Streamlit app (builds the data itself if `data/` is missing)

`data/` is git-ignored; everything is reproducible from seed 42.

## Commands (Windows, from the repo root)
- Activate env: `.venv\Scripts\activate`
- Full loop: `python generate_data.py && python infer.py && python evaluate.py`
- App: `streamlit run app.py`

## Current baseline (threshold 0.75), see docs/experiments.md
- v2 data (default, seed 42): precision 95.1%, coverage 62.9%, 4,831 wrong edges.
- v2 held-out (`--seed 7`): 95.3% / 64.0%. v1 (`--v1`): 99.4% / 73.4%.
- Weakest: shared-manager teams (59% precision).
Any change must be reported against this baseline, on all three datasets,
and logged in docs/experiments.md.

## Rules for working in this repo
- The user is new to Git/terminals: explain each change, then give exact git commands.
- Never change code without saying what changed and why.
- Evaluate on held-out companies when anything is learned or tuned (split by company_id).
- Keep the app light: Streamlit Cloud free tier has ~1 GB RAM. No torch in requirements.txt.
- Research notes: `docs/research_models.md`.
