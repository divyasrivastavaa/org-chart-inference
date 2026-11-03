# Research: decision models and GLiNER for org chart inference

Written 3 Nov 2026. Baseline to beat (rules, threshold 0.75): precision 99.1%,
coverage 73.1%, 950 wrong edges out of 110,036.

The pipeline has two places where a model could help:

| Stage | Today | Candidate model | What it would improve |
|---|---|---|---|
| A. Title parsing (title -> level, function, team) | regex `EXPANSIONS` + keyword rules | GLiNER / GLiNER2 | robustness to title wording the regex has never seen |
| B. Edge confidence (is this manager right?) | 0.95 minus hand-set penalties | Decision tree / gradient boosting | calibrated confidence, a threshold learned from data |

---

## 1. Decision models (stage B), recommended first

### Framing
Every edge the rules propose becomes one training row. The label is
`pred_manager_id == true_manager_id` (1 or 0). The model's `predict_proba` becomes the confidence.

### Features (all come from data we already compute, none come from the answer key)
- level of the employee, level of the proposed manager, gap between them
- number of empty layers skipped, number of team-mismatch layers skipped
- employee has a team (yes/no), function came from the title or from the department fallback
- size of the candidate pool at the matched layer
- number of unparsed titles in the same function, company size, function size
- title was abbreviated or changed by normalisation (yes/no)

### Avoiding leakage
- Split **by company**, not by row (`GroupShuffleSplit` / `GroupKFold` on `company_id`).
  People in one company share the same structure, so a row split would leak.
- 3 parts: train (60% of companies) -> fit; validation (20%) -> pick threshold;
  test (20%) -> report the final numbers once.

### Model choice
| Model | Pros | Cons |
|---|---|---|
| `DecisionTreeClassifier(max_depth=4, min_samples_leaf=200)` | easy to read (`export_text` gives the rules for the README), each leaf is a probability | coarse: only a few distinct confidence values |
| `HistGradientBoostingClassifier` + `CalibratedClassifierCV(method="isotonic")` | usually ranks edges better and gives smoother, calibrated scores | harder to explain |
| `LogisticRegression` | simple baseline | misses interactions (e.g. "no team" only matters at IC level) |

Plan: start with the tree (it is the story for the interview), and report boosting next to it.

### How to measure
- Brier score and a calibration table (predicted vs actual accuracy per band). Today the
  0.5-0.7 band is ~0% correct, so calibration is the clearest win.
- Precision-coverage curve on the test companies, compared with the rules at the same coverage.

### Stretch: learn the choice, not only the confidence
Score **every** candidate manager (same function, more senior), not only the one the
rules picked. Take the top candidate if its probability is above the threshold **and** clearly
above the runner-up; otherwise abstain. This could recover some of the abstentions
("2 possible managers in team").

### Circularity caveat (likely interview question)
The model learns the mess our own generator creates. To test how well it generalises,
**train on one mess setting and test on another**, e.g. train on today's data, test on the
upgraded generator (2-manager teams, missing middle managers, "Founder & CEO", contractors).
A large drop is an honest finding worth reporting.

---

## 2. GLiNER (stage A), optional, time-boxed experiment

### What it is
GLiNER is an open-source (Apache-2.0) family of small encoder models for **zero-shot named
entity recognition**: you pass any label names (e.g. `"seniority"`, `"department"`, `"team"`)
and it returns matching text spans with a score. It runs on CPU.
Install: `pip install gliner`; e.g. `GLiNER.from_pretrained("gliner-community/gliner_small-v2.5")`,
then `model.predict_entities(text, labels, threshold=0.5)`.

**GLiNER2** (Fastino) adds text classification and structured (schema) extraction in one
model: `pip install "gliner2[local]"`. Classification fits our problem better than NER,
e.g. classify a title into one of 8 seniority levels and one of 10 functions.

### Where it fits here
1. **Fallback parser:** run only on titles the regex can't parse (level or function unknown).
2. **Team extraction:** pull the team name out of titles that don't use the
   `role, team` pattern (e.g. "Platform Engineering Lead").
3. **Out-of-the-box data idea:** add a free-text field to the generator (a LinkedIn-style
   headline: "Leading the payments squad under our VP Engineering"). GLiNER extracts team and
   manager hints from it. This adds signal that titles alone can't give.

### Practical constraints
- It needs PyTorch (about 1 GB+ to install). Streamlit Community Cloud's free tier has about
  1 GB RAM, so **GLiNER must not run inside the app**. Run it offline and save the results
  as a small CSV lookup (`title -> level, function, team`) that the app reads.
- There are only **5,375 unique titles** in 152k rows, so running once per unique title
  takes minutes on a laptop CPU.
- Titles are 2-6 words with little context, while NER models are trained on sentences.
  Accuracy on bare titles has to be measured, not assumed.

### How to measure
`truth.csv` has `true_level`, `true_function` and `true_team`, so we can score
**regex vs GLiNER vs hybrid (regex first, GLiNER as fallback)** on parse accuracy, then
check whether a better parse changes edge precision and coverage.
Honest expectation: the regex was written for this generator's mess, so it will likely win on
today's data. GLiNER's value shows up on title formats the regex was never written for. Test
that by adding new title variants to the generator **after** the regex is frozen.

---

## Recommendation and order
1. Data realism upgrades (to-do 2a): they create the harder test set both models need.
2. Decision tree confidence with a company-level split (2b): directly improves the main metric
   and the "how do you know it works" story. Light: only adds `scikit-learn`.
3. GLiNER experiment, time-boxed to half a day, offline only. Report the result either way.
   Put its dependency in `requirements-research.txt`, not `requirements.txt`.

## Sources
- GLiNER: https://github.com/urchade/GLiNER
- GLiNER2: https://github.com/fastino-ai/GLiNER2 , https://pypi.org/project/gliner2/
- scikit-learn: `DecisionTreeClassifier`, `HistGradientBoostingClassifier`,
  `CalibratedClassifierCV`, `GroupKFold`: https://scikit-learn.org/stable/
