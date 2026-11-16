# Rules vs Gemini judge

- **Model:** `gemini-3.5-flash-lite`, Gemini API free tier.
- **Daily free-tier limit for this model and key:** at least 143 requests per day (all 143 questions fitted in one day; Google AI Studio shows the exact number).
- **Run:** 16 Nov 2026 by `scripts/compare_judges.py`, threshold 0.7 for both judges.

Gemini picks from the short list of candidate managers our rules find (at most 8: the rules' own pick, more senior people in the same function or unit, the top of the organisation), or answers null.

| Dataset | Judge | People | Lines drawn | Right | Precision | Coverage |
|---|---|---|---|---|---|---|
| Benchmark seed 7 (held out) | Rules | 50 | 34 | 34 | 100.0% | 68.0% |
| Benchmark seed 7 (held out) | Gemini | 50 | 33 | 32 | 97.0% | 66.0% |
| UK test half | Rules | 50 | 21 | 16 | 76.2% | 42.0% |
| UK test half | Gemini | 50 | 27 | 19 | 70.4% | 54.0% |
| ASX/NZX (all with an answer) | Rules | 43 | 36 | 36 | 100.0% | 83.7% |
| ASX/NZX (all with an answer) | Gemini | 43 | 43 | 42 | 97.7% | 100.0% |

**Ceiling for Gemini:** how often the true manager is in the candidate list it is given (it can't do better than this):

- Benchmark seed 7 (held out): 72.0%
- UK test half: 56.0%
- ASX/NZX (all with an answer): 97.7%

**Read with care:**
- **Small samples.** With 50 people, one person changes precision or coverage by about
  2 points, so small differences are noise.
- **ASX/NZX is easy for Gemini.** Every confirmed line there goes to the CEO, and the
  candidate list always includes the top of the organisation. For most people the list is
  just the CEO, so Gemini mainly has to confirm that one candidate.

Gemini API calls in this run: 163 (the rest came from the cache, `data/gemini_cache.json`).
