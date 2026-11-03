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
