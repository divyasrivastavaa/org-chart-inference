"""
title_check.py  -  spelling check for job titles typed in the app (Add people, Promote).
Compares each word with the words our title rules know, using difflib (no API). It only
suggests: the person can always keep what they typed. Names are never checked.
"""
import difflib
import re

import infer

# Common title words, on top of the ones the rules in infer.py use
COMMON = """
account accountant accounting acquisition administrator administration advisor adviser
agent analyst analytics apprentice architect assistant associate attorney auditor
backend brand business buyer
chain chair chairman chairperson chief clerk coach commercial communications compliance
consultant content contract contractor controller coordinator core corporate counsel
creative customer
data delivery deputy design designer developer development devops digital director
division
editor engineer engineering enterprise executive experience
facilities field finance financial founder freelance frontend full
general generalist global government graduate group growth
head human
information infrastructure innovation inspector insurance intelligence intern
international investment investor
junior
lawyer lead leader learning legal logistics
machine maintenance management manager managing market marketing mechanic member mobile
national network
office officer onboarding operating operational operations operator owner
partner partnerships payments payroll people performance planner planning platform policy
practice president principal procurement producer product production program programme
programmer project public purchasing
quality
recruiter recruitment regional regulatory relations representative research resources
revenue risk
sales science scientist secretary security senior service services software solutions
specialist stack staff strategic strategy student success supervisor supply support
surveyor systems
talent team technical technician technology tester trainee training treasurer treasury
vice
warehouse writer
advisory analysis analytical architecture audit cluster communication communities consult
consultation contact contracted control coordination corporation customs direct direction
directorate grow inspectorate integration intelligent internal making operation partnering
partnership payment principles protection regions regulation regulations regulator
relationship resolutions resource revaluations secretariat solution special strategies
strategist supporting system technologies test trading voice
commodities community consul principle protecting reputation resolution
"""
KNOWN = set(COMMON.split())
for text in [*infer.C_SUITE, *(w for w, _ in infer.FUNCTION_WORDS),
             *(r for _, r in infer.EXPANSIONS), *infer.DEPT_TO_FUNCTION]:
    KNOWN.update(re.findall(r"[a-z]{4,}", text.lower()))
KNOWN = sorted(KNOWN)
_KNOWN_SET = set(KNOWN)
CUTOFF = 0.8                      # how close a word must be ("cheif" / "chief" = 0.8)


def _match_case(typed, word):
    if typed.isupper():
        return word.upper()
    return word.capitalize() if typed[0].isupper() else word


def _fix_word(m):
    w = m.group(0)
    low = w.lower()
    if len(w) < 4 or (w.isupper() and len(w) <= 4) or (w != w.upper() and w[1:] != w[1:].lower()):
        return w                  # short words, acronyms (CEO), mixed case (DevOps)
    if low in _KNOWN_SET or (low.endswith("s") and low[:-1] in _KNOWN_SET):
        return w
    close = difflib.get_close_matches(
        low, [k for k in KNOWN if k[0] == low[0] and abs(len(k) - len(low)) <= 2], n=1,
        cutoff=CUTOFF)
    return _match_case(w, close[0]) if close else w


def suggest(title):
    """The title with likely typos fixed, or None if nothing looks misspelled."""
    if not isinstance(title, str):
        return None
    fixed = re.sub(r"[A-Za-z]+", _fix_word, title)
    return fixed if fixed != title else None
