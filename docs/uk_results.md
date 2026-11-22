# UK government organograms: results

> **Offline evidence.** This dataset was removed from the database and the app on 22 Nov 2026.
> The files and these results remain as evidence of accuracy on real data.

Third dataset, added 16 Nov 2026: real organisations with a **real, complete answer key**.

Contains public sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

## The data

- **Source:** data.gov.uk, "Organogram of Staff Roles & Salaries", the newest senior-staff
  CSV of 12 organisations: 7 departments and 5 agencies / public bodies. They were chosen because
  each file downloads and parses cleanly, has exactly one top post and at least 20 posts,
  and every "Reports to Senior Post" points to a post in the same file.
- **Raw files:** `data/raw/uk/` (`scripts/download_uk.py`, pinned URLs, downloaded
  2026-11-16). The dataset is rebuilt offline by `scripts/build_uk.py`.
- **2,281 posts**, after dropping 187 vacant, eliminated or "Not in post" posts.
- **What the model sees** (`data/uk/employees.csv`): name (75% are "Name withheld N"), job
  title and unit. It does **not** see "Reports to Senior Post" (the answer), grade, salary,
  pay band, FTE or contact details. Post references are replaced by new IDs in shuffled
  order, so an ID can't hint at the hierarchy.
- **Answer key** (`data/uk/truth.csv`): each post's manager from "Reports to Senior Post".
  The top post has none. 13 people report to a post that was dropped as vacant. The right
  answer for them is "no line" (scenario `manager_vacant`).
- Withheld names are numbered ("Name withheld 1", "2" …). Otherwise two withheld Deputy
  Directors in one unit would look like one duplicated row and be merged.

## Tune / test split (by organisation, fixed before looking at any errors)

The 12 organisations were sorted by size and paired. A fixed random seed (7) sent one of
each pair to **tune** and the other to **test**. All fixes below were designed by looking at
the tune half only.

| Half | Organisations | Posts |
|---|---|---|
| Tune | HM Revenue and Customs, Defra, DESNZ, NHS Blood and Transplant, Homes England, UK Atomic Energy Authority | 1,167 |
| Test | Home Office, DWP, FCDO, Environment Agency, DCMS, Valuation Office Agency | 1,114 |

## Before: our rules, unchanged (threshold 0.7)

| Half | People with a manager | Lines drawn | Right | Wrong | Precision | Coverage |
|---|---|---|---|---|---|---|
| Tune | 1,161 | 12 | 4 | 8 | 33.3% | 1.0% |
| Test | 1,108 | 0 | 0 | 0 | – (no lines) | 0.0% |
| **All 12** | 2,269 | 12 | 4 | 8 | **33.3%** | **0.5%** |

Why, on the tune half:
- **84% of titles had no function.** Our rules look for a manager in the *same function*,
  found from words like "engineering" or "sales". Civil-service titles rarely have them
  ("Deputy Director Energy Systems and Networks"), so 980 of 1,167 were "title could not
  be parsed".
- **"Deputy Director" was read as the same level as "Director"** (both contain "director"),
  so a Deputy Director could never find their Director. "Director General" and "Permanent
  Secretary" weren't recognised.
- **The confidence tree was overconfident:** it gave every UK line 0.95+, but on the tune
  half only 69% were right (after the fixes below). It was trained on Benchmark data, where
  these features mean something else.

## The fixes (in `infer.py`)

1. **Civil-service ladder:**
   - "Permanent Secretary" and plain "Chief Executive" = the top (level 1).
   - "Director General" and "Second Permanent Secretary" = level 2.
2. **Director above Deputy Director:** in an organisation that has Deputy Directors or
   Directors General, a plain "Director" is one level above a Deputy Director (level 3).
   Deputy Directors, Assistant Directors and "Head of" stay at level 4.
3. **The organisation's own unit is the function:** when the department isn't one of our
   standard function names, the unit (e.g. "Customer Services Group") is used as the
   function. On the tune half, 82% of managers are in the same unit as their reports.
4. **Measured confidence for these lines:** for lines whose function came from a unit,
   the confidence is the share that was right on the tune half, per step:

   | Step (person → manager) | Right on the tune half | Confidence | Drawn at 0.7? |
   |---|---|---|---|
   | level 2 → the top | 20 of 24 | 0.833 | yes |
   | Deputy Director → Director (4 → 3) | 101 of 121 | 0.835 | yes |
   | Director → Director General (3 → 2) | 51 of 81 | 0.630 | no |
   | other steps (fewer than 20 lines) | – | 0 | no |

Tried and **not** used, because the tune half showed they don't help:
- **Matching a Deputy Director to the Director whose title names the same topic** ("Deputy
  Director Hydrogen" → "Director Hydrogen"): 80% right overall.
- **The same match as a tie-break when several Directors share a unit:** only 37% right.

## After (threshold 0.7)

| Half | People with a manager | Lines drawn | Right | Wrong | Precision | Coverage |
|---|---|---|---|---|---|---|
| Tune (fixes designed here) | 1,161 | 146 | 122 | 24 | 83.6% | 12.6% |
| **Test (never looked at)** | 1,108 | 280 | 238 | 42 | **85.0%** | **25.3%** |
| All 12 | 2,269 | 426 | 360 | 66 | 84.5% | 18.8% |

- **The test half holds up** (85.0% vs 83.6% on the tune half), so the fixes are not just
  fitted to the tune half.
- **Confidence is honest again:** lines scored ~0.83 were right 84.3% of the time (420
  lines, all 12 organisations).
- **By level** (all 12):
  - Directors General: 97.6% precision, 44% coverage.
  - Deputy Directors: 87.2% precision, 16% coverage.
  - Directors: few lines (7%), because "Director → Director General" (63% right) is below
    the threshold.
- **Vacant managers:** 4 of the 13 people whose manager post is vacant got a line, and
  those 4 are wrong by definition. The data gives no way to tell that the post above
  them is empty.
- **Still lower precision than the Benchmark (~95%)**, by design: at threshold 0.7 (a wrong
  line costs ~2.3 times a missing one) a line is worth drawing when it is right at least
  70% of the time, and these are right ~84% of the time.

## Benchmark and ASX/NZX: unchanged

| Dataset | Before | After |
|---|---|---|
| Benchmark seed 42 | 95.2% / 62.7%, 4,727 wrong, Brier 0.0433 | **same** |
| Benchmark seed 7 (held out) | 95.4% / 63.7%, 4,411 wrong, Brier 0.0420 | **same** |
| ASX/NZX (43 confirmed lines) | 36 of 36 right, 84% coverage | **same** |

The fixes only touch civil-service titles and non-standard unit names. Neither appears in
the Benchmark (whose departments are standard names or blank) or the ASX/NZX data
(blank departments).

## Limitations

- **12 organisations, one snapshot each.** The step confidences come from 6 of them.
- **Units differ in size.** HMRC's 10 units are whole Director General groups, while DESNZ
  has 51 smaller units. In large units several Directors compete, and the rules abstain.
- **The senior-staff file stops at Deputy Director level.** Junior staff aren't included, so
  this tests only the top 4–5 levels.
