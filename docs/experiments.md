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

## E5. Default threshold 0.7 instead of 0.8 (16 Nov 2026)
Same tree, only the cut-off moved: a wrong line now costs about 2.3 times a missing one,
so draw when P(correct) >= 2.3 / 3.3 = **0.7**. Two leaves change from "abstain" to "draw":
P = 0.795 (767 training edges, a skipped empty layer in a big function) and P = 0.789
(3,440 edges, 2+ teamless managers in a big company: the shared-manager-team signal).

| Dataset | Threshold | Precision | Coverage | Wrong edges | Brier |
|---|---|---|---|---|---|
| v2 seed 42 (app) | 0.8 | 96.3% | 58.7% | 3,465 / 92,408 | 0.0433 |
| v2 seed 42 (app) | **0.7** | **95.2%** | **62.7%** | 4,727 / 98,684 | 0.0433 |
| v2 seed 7 (held out) | 0.8 | 96.3% | 60.6% | 3,399 / 91,050 | 0.0420 |
| v2 seed 7 (held out) | **0.7** | **95.4%** | **63.7%** | 4,411 / 95,794 | 0.0420 |
| real (43 confirmed lines) | 0.8 | 100% (36/36) | 84% | 0 | – |
| real (43 confirmed lines) | **0.7** | **100% (36/36)** | **84%** | 0 | – |

- Seed 42: +6,276 lines, of which 79.9% right. That matches the leaves' 0.79-0.80, so the
  confidence stays honest. Brier doesn't change (the confidences themselves are the same).
- Shared-manager teams: precision 63% -> 59%, because the 0.789 leaf is mostly these.
- Real data: unchanged. No confirmed line had a confidence between 0.7 and 0.8.

## E6. UK government organograms: third dataset, civil-service titles (16 Nov 2026)
Real answer key ("Reports to Senior Post") for 12 organisations, 2,281 senior posts. Split by
organisation into a tune half and a test half before looking at errors. Full write-up:
`docs/uk_results.md`.

| UK, threshold 0.7 | Precision | Coverage | Lines (wrong) |
|---|---|---|---|
| Before, tune half | 33.3% | 1.0% | 12 (8) |
| Before, test half | – | 0.0% | 0 |
| After, tune half (fixes designed here) | 83.6% | 12.6% | 146 (24) |
| **After, test half** | **85.0%** | **25.3%** | 280 (42) |

Fixes in `infer.py`:
- **Civil-service ladder:** Permanent Secretary and Chief Executive = top; Director General = 2;
  plain Director = 3 where Deputy Directors exist; Deputy Director = 4.
- **Units as functions:** non-standard unit names are used as the function.
- **Measured confidence:** for unit-based lines, the precision measured per step on the tune
  half (the tree gave 0.95+ but only 69% were right).

Regression check: Benchmark seed 42 (95.2% / 62.7%, 4,727 wrong, Brier 0.0433), seed 7
(95.4% / 63.7%, 4,411 wrong, Brier 0.0420) and ASX/NZX (36 of 36, 84%) are **unchanged**.

## E7. Realistic Benchmark company names (17 Nov 2026)
Not a model change. Benchmark names were odd ("Atlasatlas Pty Ltd"). v2 names are now
"<word> <industry word> <suffix>" (e.g. "Kestrel Health Ltd"): 564 fictional first words
(100 hand-picked + compounds like "Heronbrook", minus famous ones), 8 industry words per
industry, Ltd / Pty Ltd / Group / Inc. Within a seed no first word repeats; the industry
word depends on seed % 8, so all 1,500 names of seeds 42, 7 and 1 are different. No doubled
words, no well-known brand words. `--v1` keeps its old names (byte-for-byte promise).

The names come from separate random generators; the old name is still drawn from the main
generator and thrown away, so every other draw is unchanged. Checked:
- employees.csv, truth.csv and predictions.csv of seeds 42 and 7 (and the seed-1 training
  data): every column except company_name is identical.
- `evaluate.py` output for seed 42 and seed 7 is identical line for line: 95.2% / 62.7%,
  4,727 wrong, Brier 0.0433; 95.4% / 63.7%, 4,411 wrong, Brier 0.0420.
- Retrained confidence tree: `models/` unchanged. ASX/NZX and UK data don't use the generator.

## E8. Phase 2: inference from the database (20 Nov 2026)
Not a model change. The same `infer.predict()` (rules, decision tree, threshold 0.7) now
also runs from the Postgres database: `refresh.py` re-infers only companies with
`needs_refresh = true` and current employees, and saves the lines to `reporting_lines`
with a `run_id`. Sample in the database: 20 ASX/NZX, UK U01-U02, Benchmark C001-C010
(seed 42), plus 50 senior Atlassian people from PDL. Compared with `scripts/compare_db_csv.py`.

| Same companies, threshold 0.7 | People scored | Drawn | Right | Wrong | Precision | Coverage |
|---|---|---|---|---|---|---|
| Benchmark C001-C010, database | 1,620 | 1,055 | 1,044 | 11 | 99.0% | 65.1% |
| Benchmark C001-C010, CSV | 1,620 | 1,050 | 1,039 | 11 | 99.0% | 64.8% |
| ASX/NZX (20), database = CSV | 43 | 36 | 36 | 0 | 100.0% | 83.7% |
| UK U01-U02, database = CSV | 294 | 132 | 110 | 22 | 83.3% | 44.9% |

- **ASX/NZX and UK:** identical.
- **Benchmark:** +5 lines, all right, in C001, C002, C005 and C006.
  - The cause: `infer.predict()` drops duplicate rows and keeps the first one in file
    order. In the CSV that is sometimes the `-DUP` copy, so the original person counts as
    "no line" when scored. The database holds only the originals.
  - Wrong lines are unchanged (11).
  - The CSV pipeline itself is unchanged, so the baseline numbers stay as they are.
- **Atlassian (PDL, no answer key):** 0 of 50 lines drawn, because no candidate manager was
  found for anyone. Reasons:
  - 22: a teamless "Head of" may be the VP;
  - 10: title could not be parsed;
  - 9: no more-senior person in the same function;
  - 7: 2 possible managers at level 3;
  - 2: other.

  Only senior people were fetched, so most of their managers are missing from the data;
  abstaining is the intended behaviour. Before inference, PDL department words are mapped
  to our department names (`human_resources` → `Human Resources`), so they are not treated
  like UK unit names. This maps the input only; the rules are unchanged.
- **Runs:** run 1 refreshed 33 companies, 2,194 people, 2,194 lines, 9 s, $0. Run 2
  refreshed 0 companies.
- **Held-out seed 7 and the full Benchmark:** not run from the database yet; they stay as
  CSV, so their baseline numbers are unchanged.

## E9. Phase 2: full data in the database (20 Nov 2026)
Not a model change. All 500 Benchmark companies (seed 42) and all 12 UK organisations are
now in the database too, imported once from the CSVs.
- **Import:** `scripts/load_db.py`, using Postgres COPY. 135 s; a rerun takes 19 s and adds
  0 rows.
- **Refresh:** `refresh.py` (run 11) inferred the 500 new companies in 212 s, $0.
- **Compared** with `scripts/compare_db_csv.py`, on the same companies:

| Threshold 0.7 | People scored | Drawn | Right | Wrong | Precision | Coverage |
|---|---|---|---|---|---|---|
| Benchmark seed 42 (500), database | 157,323 | 99,194 | 95,025 | 4,169 | 95.8% | 63.1% |
| Benchmark seed 42 (500), CSV | 157,323 | 98,684 | 93,957 | 4,727 | 95.2% | 62.7% |
| UK (all 12), database = CSV | 2,269 | 426 | 360 | 66 | 84.5% | 18.8% |
| ASX/NZX (20), database = CSV | 43 | 36 | 36 | 0 | 100% | 83.7% |

- **UK and ASX/NZX are identical.** The UK row is all 12 organisations together; the
  tune/test split from E6 (83.6% / 12.6% and 85.0% / 25.3%) is unchanged.
- **Benchmark: the database looks better, but the rules are the same.** The difference
  comes from how duplicate rows are scored:
  - The generator adds 1% duplicate rows (`-DUP`), and `infer.predict()` keeps the first
    copy in file order.
  - When that is a manager's `-DUP` copy, the team's lines point at the `-DUP` id.
    Scoring by id then counts them as wrong, although it is the right person.
  - In the CSV run, 581 of the 4,727 wrong lines are exactly this. Without them the CSV has
    4,146 wrong lines, close to the database's 4,169.
  - The database stores only the original ids (duplicates are skipped on import), so it
    has no such lines.
  - The duplicate scenario is therefore not tested in the database; it still is in the
    CSV pipeline.
- **Baseline unchanged:** the CSV numbers stay the baseline (95.2% / 62.7%, 4,727 wrong;
  held-out seed 7 95.4% / 63.7%).
- **7 people without a line:** in the database they are separate people with identical
  name, title, department and location as someone else in their company, so
  `infer.predict()` drops them as duplicates, the same rule as for the CSVs.

Storage and housekeeping:
- **Size:** 126 MB of the 500 MB free plan after the fixes below (estimated 177 MB before
  the load).
- **Cleanup rule in `refresh.py`:** for each company, only the reporting lines of its last
  2 refreshes are kept. Employment history is never deleted.
  - Tested on C003 with `scripts/test_cleanup.py`: change a title → refresh (0 deleted),
    change it back → refresh (115 lines of the oldest run deleted).
  - The test then undid itself; C003's lines are identical to before.
- **Lighter trigger (migration 004):** the refresh trigger rewrote the company row for every
  new job, which grew the 766-row `companies` table to 21 MB during the load. It now
  updates a company only if it isn't flagged yet. A one-time `VACUUM FULL companies` brought
  it back to 256 kB, and the database from 146 MB to 126 MB.
