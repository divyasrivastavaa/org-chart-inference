"""
judge_gemini.py  -  a second "judge": Gemini picks a person's manager.

Our rules read every title (level + function/unit). For ONE person, this sends Gemini:
  - the person's name, title and unit,
  - a short list of candidate managers built from the rules' reading of the company: the
    rules' own pick (if any), then more senior people in the same function/unit (nearest
    level first), then the top of the organisation. At most MAX_CANDIDATES.
Never the whole company, and never the answer key.
Gemini answers in JSON: manager_id (one of the candidates, or null), confidence 0-1 and a
short reason. Lines below the threshold are not drawn (same rule as our own judge).

Key and model: st.secrets["GEMINI_API_KEY"] / st.secrets["GEMINI_MODEL"] (locally the file
.streamlit/secrets.toml, which git ignores; on Streamlit Cloud the app's Secrets settings).
The key is never printed, logged, cached or written to a file.
"""
import hashlib
import json
import re
import time
from pathlib import Path

import pandas as pd

import infer

# Newest stable Flash-Lite model, on the free tier. Chosen on 16 Nov 2026 because the free
# tier of gemini-3.6-flash allows only 20 requests a day (quota error), and gemini-3.8-flash
# was overloaded (503 errors, 5-7 minutes per answer). Override with GEMINI_MODEL.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
PLACEHOLDER = "paste-your-key-here"
MAX_CANDIDATES = 8

SYSTEM = (
    "You place one person in their organisation's reporting structure. Choose this person's "
    "direct manager from the candidate list ONLY, using their job titles and units. If none of "
    "the candidates is clearly the direct manager, answer null. A wrong manager is worse than "
    "no answer, so give a low confidence when you are unsure. Levels were read from titles by "
    "simple rules (1 = top of the organisation, larger = more junior) and may be wrong.")

SCHEMA = {
    "type": "object",
    "properties": {
        "manager_id": {"type": ["string", "null"],
                       "description": "id of the chosen candidate, or null"},
        "confidence": {"type": "number",
                       "description": "probability (0 to 1) that this is the direct manager"},
        "reason": {"type": "string", "description": "one short sentence"},
    },
    "required": ["manager_id", "confidence", "reason"],
}


class JudgeError(Exception):
    """Gemini could not answer (no key, network, quota ...). The message never has the key."""


def settings():
    """(api_key or None, model) from Streamlit secrets. None when missing or still the
    placeholder. Works inside the app and in plain scripts run from the repo root."""
    key = model = None
    try:
        import streamlit as st
        key = st.secrets.get("GEMINI_API_KEY")
        model = st.secrets.get("GEMINI_MODEL")
    except Exception:                     # no secrets file at all
        pass
    if not isinstance(key, str) or not key.strip() or key.strip() == PLACEHOLDER:
        key = None
    return key, (model or DEFAULT_MODEL)


# ---------------------------------------------------------------- candidates
def with_context(co):
    """One company's prediction rows with the rules' company-level corrections applied
    (e.g. a UK "Director" one level above "Deputy Director")."""
    cols = ["employee_id", "full_name", "title", "department", "company_name", "level",
            "function", "team"]
    co = co[[c for c in cols if c in co]].copy()
    for c in ("team", "function"):          # plain Python values, as in infer.predict
        co[c] = co[c].astype(object).where(co[c].notna(), None)
    return infer.fix_with_company_context(co)


def candidates(co, person_id, rules_pick=None, limit=MAX_CANDIDATES):
    """Candidate managers for one person. co = with_context(company rows)."""
    me = co[co.employee_id == person_id].iloc[0]
    others = co[(co.employee_id != person_id) & co.level.notna()]
    if pd.notna(me.level):
        if me.level == 1:
            return co.iloc[0:0]                       # top of the organisation: nobody above
        senior = others[others.level < me.level]
        nearest = me.level - senior.level             # 1 = one level up
    else:                                             # title not read: same unit, or the top
        senior = others[(others.function == me.function) | (others.level <= 2)]
        nearest = senior.level
    same = senior[(senior.function == me.function) & pd.notna(me.function)]
    order = (list(others.employee_id[others.employee_id == rules_pick])
             + list(same.assign(d=nearest[same.index]).sort_values(["d", "employee_id"])
                    .employee_id)
             + list(others.employee_id[others.level == 1]))
    keep = list(dict.fromkeys(order))[:limit]         # no repeats, rules' pick first
    return co.set_index("employee_id").loc[keep].reset_index()


def prompt(org, me, cands):
    def fmt(r):
        return {"id": r.employee_id, "name": r.full_name, "title": r.title,
                "level": None if pd.isna(r.level) else int(r.level),
                "function_or_unit": r.function if isinstance(r.function, str) else None}
    return json.dumps({
        "organisation": org,
        "person": {"name": me.full_name, "title": me.title,
                   "unit": me.department if isinstance(me.department, str) else None},
        "candidates": [fmt(r) for r in cands.itertuples()],
    }, ensure_ascii=False)


# ---------------------------------------------------------------- the judge
class GeminiJudge:
    def __init__(self, api_key, model=DEFAULT_MODEL, cache_path=None, delay=0.0):
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:
            raise JudgeError("the google-genai package is not installed") from e
        # Time limit per request: a script can wait a minute, an app visitor shouldn't
        timeout_ms = 60_000 if delay else 20_000
        self._client = genai.Client(api_key=api_key,     # the key stays inside the client
                                    http_options=types.HttpOptions(timeout=timeout_ms))
        self.model, self.delay, self._last = model, delay, 0.0
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache = (json.loads(self.cache_path.read_text(encoding="utf-8"))
                      if self.cache_path and self.cache_path.exists() else {})
        self.calls = 0                                 # API calls made (cache hits excluded)
        self.input_tokens = self.output_tokens = 0     # from Gemini's usage info (for cost)

    def _call(self, text):
        from google.genai import errors, types
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)                           # stay inside free-tier limits
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM, temperature=0, response_mime_type="application/json",
            response_json_schema=SCHEMA)
        tries = 4 if self.delay else 2          # scripts wait longer; the app answers quickly
        for attempt in range(tries):
            try:
                self._last = time.time()
                self.calls += 1
                out = self._client.models.generate_content(model=self.model, contents=text,
                                                           config=config)
                usage = getattr(out, "usage_metadata", None)    # tokens billed for this call
                if usage is not None:
                    self.input_tokens += usage.prompt_token_count or 0
                    self.output_tokens += ((usage.candidates_token_count or 0)
                                           + (getattr(usage, "thoughts_token_count", 0) or 0))
                return json.loads(out.text)
            except errors.APIError as e:
                # The DAILY free-tier limit (e.g. 20 requests per model per day): retrying
                # can't help until it resets, so stop at once and say so
                details = json.dumps(e.details or {})
                if e.code == 429 and "PerDay" in details:
                    limit = re.search(r'"quotaValue":\s*"(\d+)"', details)
                    raise JudgeError("Gemini's daily free-tier limit for this model"
                                     + (f" ({limit.group(1)} requests per day)" if limit else "")
                                     + " is used up; it resets within 24 hours") from None
                # 429 = too many requests, 500/503/504 = Google's servers busy for a moment:
                # wait (longer each time), then try again
                if e.code in (429, 500, 503, 504) and attempt < tries - 1:
                    time.sleep((30 if self.delay else 2) * 2 ** attempt)
                    continue
                raise JudgeError(f"Gemini API error {e.code} ({e.status})") from None
            except (json.JSONDecodeError, TypeError):
                raise JudgeError("Gemini's answer was not valid JSON") from None
            except Exception as e:                     # network etc.; never echo details
                raise JudgeError(f"could not reach Gemini ({type(e).__name__})") from None

    def ask(self, org, me, cands):
        """{"manager_id": id or None, "confidence": 0..1, "reason": str} for one person."""
        if cands.empty:
            return {"manager_id": None, "confidence": 0.0, "reason": "no candidate managers"}
        text = prompt(org, me, cands)
        key = hashlib.sha256(f"{self.model}\n{SYSTEM}\n{text}".encode()).hexdigest()
        if key not in self.cache:
            self.cache[key] = self._call(text)
            if self.cache_path:                        # saved after every call: reruns resume
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(json.dumps(self.cache), encoding="utf-8")
        ans = self.cache[key]
        mgr = ans.get("manager_id")
        if mgr not in set(cands.employee_id):          # only a candidate counts
            mgr = None
        try:
            conf = min(max(float(ans.get("confidence", 0)), 0.0), 1.0)
        except (TypeError, ValueError):
            conf = 0.0
        return {"manager_id": mgr, "confidence": conf if mgr else 0.0,
                "reason": str(ans.get("reason", ""))[:200]}


def place(pred, person_ids, judge):
    """Ask the judge about some people in a predictions frame (infer.predict output).
    Returns {employee_id: answer}. Raises JudgeError if Gemini fails."""
    out = {}
    targets = pred[pred.employee_id.isin(person_ids)]
    for cid, people in targets.groupby("company_id"):
        co = with_context(pred[pred.company_id == cid])
        for r in people.itertuples():
            rules_pick = r.pred_manager_id if isinstance(r.pred_manager_id, str) else None
            cands = candidates(co, r.employee_id, rules_pick)
            me = co[co.employee_id == r.employee_id].iloc[0]
            out[r.employee_id] = judge.ask(r.company_name, me, cands)
    return out
