# Real-data results

Checks how the inference rules, built on synthetic data, handle real leadership titles.

## Data

- **People:** 218 executives at 20 ASX/NZX-listed companies, collected on 14 Nov 2026 from each company's own leadership page (`scripts/build_real.py`).
- **Answer key:** 43 of the 218 have a manager confirmed in writing by the company (`scripts/build_real_truth.py`). Only these 43 are scored.
  - **explicit (12):** a source about that person, e.g. "…reporting to CEO Anthony Miller".
  - **team_statement (31):** a company-level statement in an annual report or governance statement, e.g. "Executive Team is defined as the CEO and CEO Direct Reports". It is applied only to the people it clearly covers.
- **Not scored:** the other 175 people have no written source.

**Terms:**
- **Precision:** of the lines drawn, the share that are right.
- **Coverage:** the share of people who got a manager.

## Title-parsing fixes (`infer.py`)

1. A title containing "Chief Executive Officer" is level 1 (CEO). "Group CEO" was already handled.
2. A leading "Group " is removed before reading the title ("Group Chief Information Officer" is read as "Chief Information Officer"). "Group Product Manager" is the exception, because there "Group" is part of the role.
3. A title starting with "Chief" and ending with "Officer" is level 2 (C-level).
4. "Group Executive, X", "General Counsel" and "Company Secretary" titles are level 2.

## Real data

| Evidence | People | Precision before | Precision after | Coverage before | Coverage after |
|---|---|---|---|---|---|
| explicit | 12 | n/a (no lines drawn) | 100% (5 of 5) | 0% | 42% |
| team_statement | 31 | n/a (no lines drawn) | 100% (19 of 19) | 0% | 61% |
| **All** | **43** | n/a (no lines drawn) | **100% (24 of 24)** | **0%** | **56%** |

Effect of each fix (all 43):

| After fix | Lines drawn | Right | Coverage |
|---|---|---|---|
| Before | 0 | 0 | 0% |
| 1. CEO titles | 3 | 3 | 7% |
| 2. Remove "Group " | 5 | 5 | 12% |
| 3. "Chief … Officer" | 6 | 6 | 14% |
| 4. Group Executive / General Counsel / Company Secretary | 24 | 24 | 56% |

**Caution:** 24 lines is a small sample, so a single wrong line would lower precision by about 4 points.

## Synthetic data (regression check)

| Dataset | Precision before | Precision after | Coverage before | Coverage after | Wrong edges after |
|---|---|---|---|---|---|
| Seed 42 (app data) | 96.3% | 96.3% | 58.7% | 58.7% | 3,465 (unchanged) |
| Seed 7 (held out) | 96.3% | 96.3% | 60.6% | 60.6% | 3,399 (unchanged) |

The Brier score is also unchanged (0.0433 on seed 42, 0.0420 on seed 7), so the fixes did not affect the synthetic results.

## Titles still read wrong

Level 1 = CEO. Level 2 = reports to the CEO. Level 4 = Director. Level 7 = the model's default when it doesn't recognise a title.

| Title | Company | Model reads | Should be |
|---|---|---|---|
| Chief Executive, Business & Wealth | Westpac | 7 (not parsed) | 2 |
| Chief Executive, Westpac Institutional Bank | Westpac | 7 (not parsed) | 2 |
| Chief Executive, Consumer | Westpac | 7 (not parsed) | 2 |
| Chief Executive Officer, Westpac New Zealand | Westpac | 1 | 2 |
| Group Chief Financial Officer and Group Executive, Strategy | NAB | 7 | 2 |
| Chief Risk Officer and Head of Risk Management Group | Macquarie | 4 (not parsed) | 2 |
| Managing Director, Woolworths Retail | Woolworths | 4 (not parsed) | 2 |
| Managing Director, US | Xero | 4 (not parsed) | below its manager, the Chief Business Officer |

### Divisional CEOs read as a second CEO

Fix 1 also reads divisional CEO titles as level 1. That leaves 4 companies with two "CEOs", and the model then correctly abstains for everyone who reports to a CEO:

| Company | Titles read as CEO |
|---|---|
| Westpac | Managing Director and Chief Executive Officer · Chief Executive Officer, Westpac New Zealand |
| ANZ | Group Chief Executive Officer · Group Executive and Chief Executive Officer of New Zealand |
| Macquarie | Managing Director and Chief Executive Officer of Macquarie Group Limited · …of Macquarie Bank Limited |
| Fortescue | Chief Executive Officer, Fortescue Metals · Chief Executive Officer, Growth and Energy |

This blocks all 12 confirmed Westpac lines, 3 at ANZ and 1 at Macquarie.

**Possible next fix:** treat a CEO title followed by a comma or "of X" as a divisional CEO (level 2). Like any rule change, it must be re-checked on all synthetic datasets.

## How to reproduce

```
python scripts/build_real.py
python scripts/build_real_truth.py
python infer.py data/real
python evaluate.py data/real
```
