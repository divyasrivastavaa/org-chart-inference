# Org Chart Inference

**Live app:** <paste your Streamlit link here>

Turns a flat list of employees (name + job title) into an org chart, with a confidence score on every reporting line. When the evidence isn't strong enough, it leaves the manager blank — a wrong edge is worse than no edge.

## Results (500 companies, 151,060 people)

| Threshold | Precision | Coverage |
|---|---|---|
| 0.60 | 98.5% | 73.5% |
| **0.75 (default)** | **99.1%** | **73.1%** |
| 0.90 | 99.5% | 65.7% |

- **Precision** = of the reporting lines we output, the share that are correct.
- **Coverage** = the share of people (excluding CEOs) who got a manager.
- At the default threshold: 950 wrong lines out of 110,036.

<update these numbers with your own run of `python evaluate.py`>

## How to run

```
pip install -r requirements.txt
python generate_data.py   # builds data/employees.csv and data/truth.csv
python infer.py           # builds data/predictions.csv
python evaluate.py        # prints precision / coverage
streamlit run app.py      # opens the app
```

## Approach

1. **Generate the truth first.** `generate_data.py` builds each company top-down (CEO → C-level → VP → Director → Manager → ICs), so every person has a known true manager. It then writes a messy copy with the manager column removed.
2. **Make it messy.** Abbreviations (Sr., Mgr, SWE, CTO), synonyms (Head of / Director, Lead / Senior), random upper/lower case, team names dropped, 25% missing departments, 2% missing titles, 1% duplicate rows.
3. **Parse titles** into seniority level (1–8), function and team, using rules.
4. **Infer managers.** For each person, walk up the levels in their function and look for exactly one candidate (matching on team for managers and directors). If there are several candidates, or the evidence is weak, abstain.
5. **Score confidence**, starting at 0.95 and subtracting for each risk signal (skipped layer, missing team, unparsed titles nearby). Apply a threshold.
6. **Evaluate** against the hidden answer key.

## Key decisions

- **Precision over coverage.** Abstain rather than guess, as the brief asks.
- **Individual contributors never manage.** This keeps juniors from being assigned to seniors.
- **Team is the strongest signal** below VP level.
- **Confidence is checked, not just assumed.** Edges scored 0.8+ were ~99% correct, while edges scored 0.5–0.7 were almost always wrong, which is why the threshold is set at 0.75.

## Limitations and next steps

- The data is synthetic and the rules were written knowing its structure. Real data would be messier (dotted lines, matrix orgs, contractors).
- Better next steps: an LLM to parse unusual titles, using LinkedIn-style tenure and location as tie-breakers, and learning the confidence weights from labelled data instead of hand-setting them.

## How I used AI

<your own notes: what you asked Claude/ChatGPT for, what you changed or fixed yourself, what you learned>
