---
description: Regenerate data, run inference and evaluation, compare with the baseline
---
Run the full pipeline from the repo root using the virtual environment:

1. `.\.venv\Scripts\python.exe generate_data.py`
2. `.\.venv\Scripts\python.exe infer.py`
3. `.\.venv\Scripts\python.exe evaluate.py`

Then report, in a short table: precision, coverage and wrong edges at the current threshold,
next to the baseline in CLAUDE.md (99.1% / 73.1% / 950). Call out any level whose precision
dropped, and say in one line whether the change is worth keeping. $ARGUMENTS
