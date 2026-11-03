# Experiment log

Every change is measured with `generate_data.py` -> `infer.py` -> `evaluate.py`
(threshold 0.75 unless stated). Newest entries at the bottom.

## E1. Baseline rules on v1 data (3 Nov 2026)
| Precision | Coverage | Wrong edges | of which skip-level |
|---|---|---|---|
| 99.1% | 73.1% | 950 / 110,036 | 378 |

## E2. Same rules, unchanged, on v2 (realistic) data (3 Nov 2026)
The rules were written while looking at v1, so this is a test on data they have never
seen. It is the honest answer to "aren't you just tuning on your own generator?".

| Precision | Coverage | Wrong edges | of which skip-level | to dotted-line manager |
|---|---|---|---|---|
| **84.0%** | **50.0%** | 12,607 / 78,608 | 4,162 | 70 |

By scenario (people whose case is...):

| Scenario | People | Precision | Coverage | Why |
|---|---|---|---|---|
| normal | 86,653 | 92.6% | 57.8% | their *manager's* title may be a variant ("Team Lead") the rules can't read |
| title_variant | 33,284 | 83.4% | 51.5% | "Founder & CEO", "Managing Director", "Head of Engineering" (a VP), "Customer Success Manager" (an IC) |
| shared_team | 22,229 | 52.5% | 33.0% | if one of the two managers is unreadable, everyone goes to the other |
| manager_missing | 9,005 | 0% | 25.0% | true boss not in the data; any edge drawn is wrong (mostly skip-level) |
| contractor | 5,377 | 87.5% | 29.9% | "(Contractor)" suffix breaks team parsing |
| dotted_line | 775 | 21.9% | 20.0% | title/department show the business team, solid line is in Data |

Confidence is now badly calibrated: the 0.8-0.9 band is right 79% of the time, not ~99%.
Next: fix the parser for the new title patterns, and learn confidence from features (E3+).

## E3. Rules updated for real-world titles (3 Nov 2026)
Changes to `infer.py`:
- Founder wording stripped ("Founder & CEO" -> CEO); "Managing Director" = CEO;
  more C-suite names (CHRO, Chief Commercial Officer, Finance Director, CPO ...).
- Fixed a v1 bug: "CPO - People" never matched (normalising turns " - " into ", ").
- Level read by keyword priority (VP > Director > Junior/Intern > Manager > Senior), so
  "Senior Director" is a Director, not a senior IC; SVP / "Senior Vice President of" = VP.
- "Lead" by position: "Lead Data Analyst" = senior IC; "Team Lead" / "Engineering Lead" = manager.
- "Staff", "Principal", "... III" = senior IC; "Graduate", "Trainee", "Intern" = junior.
- Contractor markers stripped (kept as a flag for later); "Account Manager" = IC.
- New IC titles mapped to functions (Recruiter, BI Analyst, QA Engineer, SDR ...).
- **Company context** (new): a team name maps to the function most of its members are in,
  which fills blank functions. 3+ "managers" in one team are really ICs (e.g. Customer
  Success Managers).
- **New abstentions**: an IC never skips past the team-manager layer (their manager is
  probably missing from the data); a team that belongs to another function means an
  embedded role / dotted line; a teamless "Head of X" may be the VP, so Directors in that
  function abstain.

| Dataset | Precision | Coverage | Wrong edges | Skip-level |
|---|---|---|---|---|
| v1 (seed 42) | 99.4% (was 99.1%) | 73.4% (was 73.1%) | 621 (was 950) | 81 |
| v2 seed 42 (dev) | 95.1% (was 84.0%) | 62.9% (was 50.0%) | 4,831 | 436 |
| v2 seed 7 (**held out**, never looked at while writing rules) | 95.3% | 64.0% | 4,558 | 401 |

A mistake caught by the v1 regression check: stripping "Group " (for "Group CEO") also
turned "Group Product Manager" into an IC. Restricted to "Group CEO".

Remaining weak spot, shared-manager teams (59% precision): 2,059 of the 2,388 wrong edges
there happen because the *other* manager's title lost its team, so the rules see one
candidate. This is a confidence problem rather than a parsing one -> E4: learn confidence
with a decision tree, using features like "teamless manager in my function".

## E4. Learned confidence: decision tree replaces hand-set penalties (3 Nov 2026)
Setup (`train_confidence.py`):
- Trained on a **separate dataset (v2, seed 1)**. The app data (seed 42) and the held-out
  set (seed 7) are never seen in training, so the precision shown in the app isn't inflated.
- One row per proposed edge, label = edge is correct. Split **by company**: 400 train /
  100 validation companies.
- 13 features from the inference evidence: levels, layers skipped (empty vs. not-my-team),
  has team, contractor, size of candidate pool, **teamless managers at the same level**,
  unparsed titles nearby, function and company size, manager's span vs. similar managers.
- `DecisionTreeClassifier(max_depth=4, min_samples_leaf=200)`: 9 leaves, readable in
  `models/confidence_tree.txt`. Depth 8 had a slightly better validation Brier score
  (0.0440 vs 0.0465), but its 57 leaves can't be read; I chose readability.
- **Threshold not tuned, derived:** if a wrong edge costs 4x a missing edge, draw the edge
  when P(correct) > 4/5 = **0.8**.
- Saved as JSON and walked by ~10 lines in `infer.py`: the app needs no scikit-learn. The
  training script asserts that the JSON walker agrees with scikit-learn.

| Dataset | Confidence | Precision | Coverage | Wrong edges | Brier (lower = better) |
|---|---|---|---|---|---|
| v2 seed 42 (app) | hand-set rules @0.75 | 95.1% | 62.9% | 4,831 | 0.0538 |
| v2 seed 42 (app) | **tree @0.8** | **96.3%** | 58.7% | **3,465** | **0.0433** |
| v2 seed 7 (held out) | hand-set rules @0.75 | 95.3% | 64.0% | 4,558 | 0.0511 |
| v2 seed 7 (held out) | **tree @0.8** | **96.3%** | 60.6% | **3,399** | **0.0420** |

Trade-off: 28% fewer wrong edges for ~4 points less coverage, which is what a 4:1 cost asks for.
Calibration on validation companies: the 0.79 band is right 79.6%, the 0.98 band 97.4%. The
hand-set scores were not like this (their 0.6-0.7 band was 0% right).

What the tree learned (worth discussing):
- Skipping a layer that exists for other teams -> **0% correct** (965 edges). The hand-set
  rule gave these 0.70; the tree says never draw them.
- 2+ teamless managers at the same level in a big company -> 79% -> abstain. This is the
  shared-manager-team signal.
- Only 5 of 13 features are used at depth 4; the span ratio isn't used.

Still weak: shared-manager teams (63% precision, coverage cut to 16%) and manager-missing
cases (every edge drawn there is wrong by definition: 13% coverage). The features can't
fully tell these apart from normal teams.
