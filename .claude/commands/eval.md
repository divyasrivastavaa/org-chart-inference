---
description: Regenerate data, run inference and evaluation, compare with the baseline
---
From the repo root, using the virtual environment (`.\.venv\Scripts\python.exe`):

1. App data: `generate_data.py`, `infer.py`, `evaluate.py`
2. Held out: `generate_data.py --seed 7 --out data/holdout_seed7`,
   `infer.py data/holdout_seed7`, `evaluate.py data/holdout_seed7`
3. If the change touches features or rules, retrain first: `train_confidence.py`

Report a short table: precision, coverage, wrong edges and Brier score for both datasets,
next to the baseline in CLAUDE.md. Call out any scenario or level whose precision dropped,
say in one line whether the change is worth keeping, and log it in docs/experiments.md.
$ARGUMENTS
