"""
infer.py  -  Steps 4 and 5
Reads data/employees.csv (messy, no manager column) and writes data/predictions.csv:
one row per person with a predicted manager + confidence, or a blank manager + reason.
"""
import re

import pandas as pd

# ---------------------------------------------------------------- Step 4: clean titles
# Applied in order, on lower-case text, as whole words
EXPANSIONS = [
    (r"\bsr\.?|\bsnr\b", "senior"), (r"\bjr\.?", "junior"), (r"\bmgr\b", "manager"),
    (r"\bdir\.", "director of"), (r"\bhead of\b", "director of"),
    (r"\bsvp\b,?( of)?", "vp of"), (r"\bvice president,?( of)?", "vp of"),
    (r"\beng\b", "engineering"),
    (r"\bmktg\b", "marketing"), (r"\bops\b", "operations"), (r"\bswe\b", "software engineer"),
    (r"\bae\b", "account executive"), (r"\bdeveloper\b", "software engineer"),
    (r"\bassociate\b(?! *$)", "junior"),
    (r"\bhuman resources\b", "hr"),
]
# Founder / contractor wording carries no level information for the role itself
FOUNDER = r"\b(co-?founder|founder) ?& ?|\s*& ?(co-?)?founder\b|^group (?=ceo)|^president & "
CONTRACT = r"\(contractor\)|^freelance |^contract |,? *- *contract$|, contract$"

C_SUITE = {  # exact cleaned title -> (level, function)
    "ceo": (1, "Executive"), "chief executive officer": (1, "Executive"),
    "managing director": (1, "Executive"),
    "cto": (2, "Engineering"), "chief technology officer": (2, "Engineering"),
    "chief technical officer": (2, "Engineering"),
    "cro": (2, "Sales"), "chief revenue officer": (2, "Sales"),
    "chief commercial officer": (2, "Sales"), "chief sales officer": (2, "Sales"),
    "cmo": (2, "Marketing"), "chief marketing officer": (2, "Marketing"),
    "chief growth officer": (2, "Marketing"),
    "cfo": (2, "Finance"), "chief financial officer": (2, "Finance"),
    "finance director": (2, "Finance"),
    "cpo, people": (2, "People"), "chief people officer": (2, "People"),
    "chro": (2, "People"), "chief hr officer": (2, "People"),
    "coo": (2, "Operations"), "chief operating officer": (2, "Operations"),
    "cpo": (2, "Product"), "chief product officer": (2, "Product"),
    "chief data officer": (2, "Data"), "chief data & analytics officer": (2, "Data"),
    "ciso": (2, "Security"), "chief information security officer": (2, "Security"),
    "chief security officer": (2, "Security"),
    "chief customer officer": (2, "Customer Success"),
    "chief experience officer": (2, "Customer Success"),
}

# Keyword -> function. Checked in this order, so specific words come first.
FUNCTION_WORDS = [
    ("customer success", "Customer Success"), ("customer support", "Customer Success"),
    ("support specialist", "Customer Success"),
    ("security", "Security"), ("soc ", "Security"),
    ("data", "Data"), ("bi analyst", "Data"), ("product", "Product"),
    ("software", "Engineering"), ("engineering", "Engineering"), ("backend", "Engineering"),
    ("frontend", "Engineering"), ("full stack", "Engineering"), ("qa ", "Engineering"),
    ("account executive", "Sales"), ("account manager", "Sales"), ("sales", "Sales"),
    ("bdr", "Sales"),
    ("marketing", "Marketing"), ("content writer", "Marketing"),
    ("financ", "Finance"), ("accountant", "Finance"),
    ("hr ", "People"), ("people", "People"), ("recruit", "People"), ("talent", "People"),
    ("operations", "Operations"), ("logistic", "Operations"),
]
DEPT_TO_FUNCTION = {"Engineering": "Engineering", "Sales": "Sales", "Marketing": "Marketing",
                    "Finance": "Finance", "Human Resources": "People",
                    "Operations": "Operations", "Product": "Product", "Data": "Data",
                    "Security": "Security", "Customer Success": "Customer Success",
                    "Executive": "Executive"}
IC_ONLY_MANAGER_TITLES = {"account manager"}   # "manager" in the name, but an IC


def normalise(title):
    if not isinstance(title, str) or not title.strip():
        return None
    t = re.sub(r"\s+", " ", title.strip().lower())
    t = re.sub(FOUNDER, "", t).strip()
    t = t.replace(" - ", ", ")
    for pattern, repl in EXPANSIONS:
        t = re.sub(pattern, repl, t)
    return re.sub(r"\s+", " ", t).strip()


def level_of(role):
    if re.search(r"\bvp\b", role):
        return 3
    if "director" in role:
        return 4
    if re.match(r"(junior|graduate|trainee)\b", role) or re.search(r"\bintern\b", role):
        return 8
    if role in IC_ONLY_MANAGER_TITLES:
        return 7
    if "group product manager" in role or (role.endswith("manager")
                                           and "product manager" not in role):
        return 5
    if re.match(r"(senior|staff|principal)\b", role) or role.endswith(" iii"):
        return 6
    return 7


def parse_title(title, department):
    """Return (level, function, team, is_contractor) - any may be None if unknown."""
    t = normalise(title)
    if t is None:
        return None, DEPT_TO_FUNCTION.get(department), None, False
    contractor = bool(re.search(CONTRACT, t))
    t = re.sub(CONTRACT, "", t).strip(" ,")
    if t in C_SUITE:
        return (*C_SUITE[t], None, contractor)
    role, team = (t.split(", ", 1) + [None])[:2]

    # "lead" means different things depending on where it sits
    m = re.match(r"(.*?) ?team lead$", role)
    if m:                                       # "Platform Team Lead" / "Team Lead"
        role, team = "manager", (m.group(1) or team)
    elif re.match(r"lead ", role):              # "Lead Data Analyst" = senior IC
        role = "senior " + role[5:]
    elif role.endswith(" lead"):                # "Engineering Lead, Platform" = manager
        role = role[:-5] + " manager"

    level = level_of(role)
    function = next((f for word, f in FUNCTION_WORDS if word in role + " "), None)
    if function is None:
        function = DEPT_TO_FUNCTION.get(department)
    return level, function, (team.strip() if team else None), contractor


# ---------------------------------------------------------------- Step 5: infer managers
MANAGER_LEVELS = [5, 4, 3, 2]          # ICs (6-8) never manage anyone, by assumption
BASE = 0.95                            # starting confidence for a unique match
TEAM_SKIP_PENALTY = 0.25               # layer exists in my function, but not for my team
EMPTY_SKIP_PENALTY = 0.03              # whole layer missing (e.g. no VP) - normal, tiny cost
NO_TEAM_PENALTY = 0.10                 # IC matched without team evidence
UNKNOWN_PENALTY = 0.05                 # unparsed titles nearby might be the real boss
MAX_MANAGERS_PER_TEAM = 2              # 3+ "managers" in one team are really ICs (e.g. CSMs)


def fix_with_company_context(co):
    """Use what the rest of the company tells us about each title."""
    co = co.copy()
    # 1. Team -> function: teams belong to one function, so a known team fills a blank
    #    function (e.g. "Recruiter, Talent Acquisition" with no department).
    known = co[co.function.notna() & co.team.notna() & (co.function != "Executive")]
    team_func = known.groupby("team").function.agg(lambda s: s.mode().iloc[0]).to_dict()
    blank = co.function.isna() & co.team.notna()
    co.loc[blank, "function"] = co.loc[blank, "team"].map(team_func)
    co["team_function"] = co.team.map(team_func)
    # 2. Too many "managers" in one team: the title is an IC title here
    mgr = co[co.level == 5]
    counts = mgr.groupby(["function", "team"]).employee_id.transform("count")
    co.loc[counts[counts > MAX_MANAGERS_PER_TEAM].index, "level"] = 7
    return co


def infer_company(co):
    """co = one company's rows. Returns a list of prediction dicts."""
    co = fix_with_company_context(co)
    # Index once: (function, level) -> list of (employee_id, team). Fast lookups.
    index = {}
    for r in co.itertuples():
        if not pd.isna(r.level) and r.function is not None and not pd.isna(r.function):
            index.setdefault((r.function, int(r.level)), []).append((r.employee_id, r.team))
    ceos = index.get(("Executive", 1), [])
    unknown_funcs = set(co.loc[co.level.isna(), "function"])
    # "Head of Engineering" with no team: a VP, or a Director whose team was dropped?
    teamless_directors = {f: {e for e, t in pool if t is None}
                          for (f, lvl), pool in index.items() if lvl == 4}

    out = []
    for p in co.itertuples():
        pred, conf, reason = None, 0.0, ""
        if pd.isna(p.level) or p.function is None or pd.isna(p.function):
            reason = "title could not be parsed"
        elif p.level == 1:
            reason = "top of company"
        elif p.level == 2:
            if len(ceos) == 1:
                pred, conf = ceos[0][0], BASE
            else:
                reason = f"{len(ceos)} CEOs found"
        elif p.team and isinstance(p.team_function, str) and p.team_function != p.function:
            reason = f"team belongs to {p.team_function} (embedded role / dotted line?)"
        else:
            penalty = 0.0
            for lvl in [x for x in MANAGER_LEVELS if x < p.level]:
                pool = index.get((p.function, lvl), [])
                if p.level >= 6 and lvl in (3, 2):   # teams always have a manager
                    reason = "team manager not found (may be missing from the data)"
                    break
                if lvl == 3 and not pool and p.level == 4 and \
                        teamless_directors.get(p.function, set()) - {p.employee_id}:
                    reason = "a teamless 'Head of' may be the VP"
                    break
                if not pool:                          # nobody at this layer at all
                    penalty += EMPTY_SKIP_PENALTY
                    continue
                if lvl in (4, 5):                     # team-level managers
                    if p.team:
                        match = [e for e, t in pool if t == p.team]
                        if len(match) == 1:
                            pred = match[0]
                            break
                        if len(match) > 1:
                            reason = f"{len(match)} possible managers in team"
                            break
                        if any(t is None for _, t in pool):
                            reason = "candidate managers have unknown team"
                            break
                        penalty += TEAM_SKIP_PENALTY  # layer exists, not for my team
                        continue
                    if len(pool) == 1:
                        pred = pool[0][0]
                        break
                    reason = f"team unknown, {len(pool)} possible managers"
                    break
                if len(pool) == 1:                    # VP / C-level: one per function
                    pred = pool[0][0]
                    break
                reason = f"{len(pool)} possible managers at level {lvl}"
                break
            if pred is not None:
                conf = BASE - penalty
                if not p.team and p.level >= 6:
                    conf -= NO_TEAM_PENALTY
                if p.function in unknown_funcs or None in unknown_funcs:
                    conf -= UNKNOWN_PENALTY
            elif not reason:
                reason = "no more-senior person in same function"
        out.append(dict(employee_id=p.employee_id, pred_manager_id=pred,
                        confidence=round(max(conf, 0), 2), reason=reason))
    return out


def main(threshold=0.75):
    df = pd.read_csv("data/employees.csv")
    # Duplicates: same person listed twice -> keep one
    df = df.drop_duplicates(subset=["company_id", "full_name", "title", "department",
                                    "location"])
    parsed = df.apply(lambda r: parse_title(r.title, r.department), axis=1)
    df[["level", "function", "team", "is_contractor"]] = pd.DataFrame(parsed.tolist(),
                                                                      index=df.index)
    df["team"] = df["team"].astype(object).where(df["team"].notna(), None)
    df["function"] = df["function"].astype(object).where(df["function"].notna(), None)

    rows = []
    for _, co in df.groupby("company_id"):
        rows.extend(infer_company(co))
    pred = df.merge(pd.DataFrame(rows), on="employee_id")
    # Abstain below the threshold: a wrong edge is worse than no edge
    low = pred.confidence < threshold
    pred.loc[low & pred.pred_manager_id.notna(), "reason"] = "confidence below threshold"
    pred["manager_id"] = pred.pred_manager_id.where(~low)
    pred.to_csv("data/predictions.csv", index=False)
    print(f"{len(pred)} people, {pred.manager_id.notna().mean():.1%} got a manager")


if __name__ == "__main__":
    main()
