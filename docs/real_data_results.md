# Real-data results

> **Offline evidence.** This dataset was removed from the database and the app on 22 Nov 2026.
> The files and these results remain as evidence of accuracy on real data.

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
5. **Divisional CEOs:** a CEO title followed by a comma or "of X" is level 2, e.g. "Chief Executive Officer, Westpac New Zealand" or "…of Macquarie Bank Limited".
   - **Exception:** if X is the company's own name, it is still the main CEO, e.g. "…Chief Executive Officer, Wesfarmers" or "…of Macquarie Group Limited".

## Real data

| Evidence | People | Precision before | Precision after fixes 1–4 | Precision after fix 5 | Coverage before | Coverage after fixes 1–4 | Coverage after fix 5 |
|---|---|---|---|---|---|---|---|
| explicit | 12 | n/a (no lines drawn) | 100% (5 of 5) | 100% (10 of 10) | 0% | 42% | 83% |
| team_statement | 31 | n/a (no lines drawn) | 100% (19 of 19) | 100% (26 of 26) | 0% | 61% | 84% |
| **All** | **43** | n/a (no lines drawn) | 100% (24 of 24) | **100% (36 of 36)** | **0%** | 56% | **84%** |

Effect of each fix (all 43):

| After fix | Lines drawn | Right | Coverage |
|---|---|---|---|
| Before | 0 | 0 | 0% |
| 1. CEO titles | 3 | 3 | 7% |
| 2. Remove "Group " | 5 | 5 | 12% |
| 3. "Chief … Officer" | 6 | 6 | 14% |
| 4. Group Executive / General Counsel / Company Secretary | 24 | 24 | 56% |
| 5. Divisional CEOs | 36 | 36 | 84% |

**Caution:** 36 lines is a small sample, so a single wrong line would lower precision by about 3 points.

## Synthetic data (regression check)

| Dataset | Precision before | Precision after | Coverage before | Coverage after | Wrong edges after |
|---|---|---|---|---|---|
| Seed 42 (app data) | 96.3% | 96.3% | 58.7% | 58.7% | 3,465 (unchanged) |
| Seed 7 (held out) | 96.3% | 96.3% | 60.6% | 60.6% | 3,399 (unchanged) |

The Brier score is also unchanged (0.0433 on seed 42, 0.0420 on seed 7), so all five fixes left the synthetic results unchanged.

## Titles still read wrong

These 7 titles block the remaining 7 confirmed lines.

Level 1 = CEO. Level 2 = reports to the CEO. Level 4 = Director. Level 7 = the model's default when it doesn't recognise a title.

| Title | Company | Model reads | Should be |
|---|---|---|---|
| Chief Executive, Business & Wealth | Westpac | 7 (not parsed) | 2 |
| Chief Executive, Westpac Institutional Bank | Westpac | 7 (not parsed) | 2 |
| Chief Executive, Consumer | Westpac | 7 (not parsed) | 2 |
| Group Chief Financial Officer and Group Executive, Strategy | NAB | 7 | 2 |
| Chief Risk Officer and Head of Risk Management Group | Macquarie | 4 (not parsed) | 2 |
| Managing Director, Woolworths Retail | Woolworths | 4 (not parsed) | 2 |
| Managing Director, US | Xero | 4 (not parsed) | below its manager, the Chief Business Officer |

**Fortescue (not scored):** Fortescue has two divisional CEOs ("…, Fortescue Metals" and "…, Growth and Energy") and no title the model reads as its overall CEO, so nobody there is linked to a CEO. That is the safe outcome: no line is better than a guessed one.

## How to reproduce

```
python scripts/build_real.py
python scripts/build_real_truth.py
python infer.py data/real
python evaluate.py data/real
```
