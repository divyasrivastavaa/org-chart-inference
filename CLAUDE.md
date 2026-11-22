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
4. `app.py`          -> Streamlit app. Reads and writes ONLY the database (see below);
   it never reads CSV files.
5. `train_confidence.py` -> trains the edge-confidence tree on a separate seed-1 dataset,
   saves `models/confidence_tree.json` (used by infer.py) + `.txt` (readable rules)

App data: `scripts/pdl_shortlist.py` (real companies from the PDL free dataset, CC BY 4.0) ->
`scripts/load_pdl_companies.py` + `generate_pdl_people.py` (synthetic people, past jobs and
answer key; also written to `data/pdl_synth/`). The offline loop above stays for measuring.

`data/` is git-ignored (offline seeds: 42, held-out 7, training 1). `models/` IS committed
(the hosted app needs it). Benchmark, ASX/NZX and UK data are offline only, never in the app.

## Database (Phase 2): the app's only data source
- Supabase Postgres. Tables: companies, people, employments (job history), reporting_lines,
  answer_key, runs (time, credits, tokens, cost), sources, change_log, saved_queries.
  Explained in `db/README.md`; plan and steps in `docs/db_plan.md`.
- Data: 740 PDL companies (`N001`...) with 262,007 synthetic people and 77,702 past jobs, plus
  Atlassian (`P01`, 53 real people). Size 273.6 MB; measured refresh peak 338.8 MB; limit
  450 MB. Never run `VACUUM FULL` by hand: use `python scripts/compact_db.py`.
- Setup order: `db/schema.sql` once, then `db/migrations/001` ... `012` (safe to rerun),
  via `python scripts/run_sql.py <file>`. A schema change = a NEW migration file + the same
  change in `db/schema.sql`.
- Import: `python scripts/load_pdl_companies.py` (only companies not loaded yet). Infer:
  `python refresh.py` (only companies with needs_refresh; keeps each company's last 2
  refreshes; logs a runs row and Logfire spans with IDs and counts only).
  `python scripts/refresh_watch.py` does the same with a 450 MB guard.
- Code: `db/connect.py` (get_connection, get_secret), `db/read.py` (everything the app
  reads), `db/changes.py` (every write: add/remove/promote/change job, undo_last,
  save_query; history is never overwritten: close the old row, open a new one),
  `db/costs.py` (prices), `db/telemetry.py` (Logfire).
- App: open to everyone, with "Reset demo data"; PDL people show current job only; SQL tab runs as role
  app_readonly in a READ ONLY transaction (5 s, LIMIT 500; set_config refused).
- Secrets (names only): DATABASE_URL, DATABASE_PASSWORD,
  LOGFIRE_TOKEN, GEMINI_API_KEY, PDL_API_KEY. Never print, log or commit them.
- PDL: never call the API unless the user explicitly says so; use `data/pdl/cache/`.
- Tests: `python scripts/test_app.py` (read only), `python scripts/test_app_changes.py`
  (writes, then restores the database exactly), `python scripts/check_pdl_load.py`,
  `python scripts/compare_db_csv.py` (needs `python infer.py data/pdl_synth`).
- Numbers from the database (threshold 0.7): PDL companies 97.5% / 66.7%, identical to the
  CSVs (E10). This data is less messy, so it is not an improvement on the baseline below.

## Commands (Windows, from the repo root)
- Activate env: `.venv\Scripts\activate`
- Full loop: `python generate_data.py`, `python infer.py`, `python evaluate.py`
- Held out: `python generate_data.py --seed 7 --out data/holdout_seed7`,
  `python infer.py data/holdout_seed7`, `python evaluate.py data/holdout_seed7`
- Retrain confidence: `python train_confidence.py` (`python infer.py --rules` = no tree)
- App: `streamlit run app.py` (needs the database secrets; see Database below)

## Current offline baseline, see docs/experiments.md (E5, E10)
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
