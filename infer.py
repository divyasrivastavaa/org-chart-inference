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
    (r"\bvice president,?", "vp of"), (r"\beng\b", "engineering"),
    (r"\bmktg\b", "marketing"), (r"\bops\b", "operations"), (r"\bswe\b", "software engineer"),
    (r"\bae\b", "account executive"), (r"\bdeveloper\b", "software engineer"),
    (r"\blead\b", "senior"), (r"\bassociate\b(?! *$)", "junior"),
    (r"\bhuman resources\b", "hr"),
]

C_SUITE = {  # exact cleaned title -> (level, function)
    "ceo": (1, "Executive"), "chief executive officer": (1, "Executive"),
    "cto": (2, "Engineering"), "chief technology officer": (2, "Engineering"),
    "cro": (2, "Sales"), "chief revenue officer": (2, "Sales"),
    "cmo": (2, "Marketing"), "chief marketing officer": (2, "Marketing"),
    "cfo": (2, "Finance"), "chief financial officer": (2, "Finance"),
    "cpo - people": (2, "People"), "chief people officer": (2, "People"),
    "coo": (2, "Operations"), "chief operating officer": (2, "Operations"),
    "chief product officer": (2, "Product"), "chief data officer": (2, "Data"),
    "ciso": (2, "Security"), "chief information security officer": (2, "Security"),
    "chief customer officer": (2, "Customer Success"),
}

# Keyword -> function. Checked in this order, so specific words come first.
FUNCTION_WORDS = [
    ("customer success", "Customer Success"), ("security", "Security"),
    ("data", "Data"), ("product", "Product"), ("software", "Engineering"),
    ("engineering", "Engineering"), ("account executive", "Sales"), ("sales", "Sales"),
    ("marketing", "Marketing"), ("financ", "Finance"), ("hr ", "People"),
    ("people", "People"), ("operations", "Operations"),
]
DEPT_TO_FUNCTION = {"Engineering": "Engineering", "Sales": "Sales", "Marketing": "Marketing",
                    "Finance": "Finance", "Human Resources": "People",
                    "Operations": "Operations", "Product": "Product", "Data": "Data",
                    "Security": "Security", "Customer Success": "Customer Success",
                    "Executive": "Executive"}


def normalise(title):
    if not isinstance(title, str) or not title.strip():
        return None
    t = re.sub(r"\s+", " ", title.strip().lower()).replace(" - ", ", ")
    for pattern, repl in EXPANSIONS:
        t = re.sub(pattern, repl, t)
    return re.sub(r"\s+", " ", t).strip()


def parse_title(title, department):
    """Return (level, function, team) — any of them may be None if unknown."""
    t = normalise(title)
    if t is None:
        return None, DEPT_TO_FUNCTION.get(department), None
    if t in C_SUITE:
        return (*C_SUITE[t], None)
    role, team = (t.split(", ", 1) + [None])[:2]

    if role.startswith("vp"):
        level = 3
    elif role.startswith("director"):
        level = 4
    elif "group product manager" in role or (role.endswith("manager")
                                             and "product manager" not in role):
        level = 5
    elif role.startswith("senior"):
        level = 6
    elif role.startswith("junior"):
        level = 8
    else:
        level = 7

    function = next((f for word, f in FUNCTION_WORDS if word in role + " "), None)
    if function is None and role.strip() == "analyst":
        function = DEPT_TO_FUNCTION.get(department)  # "analyst" alone: trust department
    if function is None:
        function = DEPT_TO_FUNCTION.get(department)
    return level, function, (team.strip() if team else None)


# ---------------------------------------------------------------- Step 5: infer managers
MANAGER_LEVELS = [5, 4, 3, 2]          # ICs (6-8) never manage anyone, by assumption
BASE = 0.95                            # starting confidence for a unique match
TEAM_SKIP_PENALTY = 0.25               # layer exists in my function, but not for my team
EMPTY_SKIP_PENALTY = 0.03              # whole layer missing (e.g. no VP) - normal, tiny cost
NO_TEAM_PENALTY = 0.10                 # IC matched without team evidence
UNKNOWN_PENALTY = 0.05                 # unparsed titles nearby might be the real boss


def infer_company(co):
    """co = one company's rows. Returns a list of prediction dicts."""
    # Index once: (function, level) -> list of (employee_id, team). Fast lookups.
    index = {}
    for r in co.itertuples():
        if not pd.isna(r.level):
            index.setdefault((r.function, int(r.level)), []).append((r.employee_id, r.team))
    ceos = index.get(("Executive", 1), [])
    unknown_funcs = set(co.loc[co.level.isna(), "function"])

    out = []
    for p in co.itertuples():
        pred, conf, reason = None, 0.0, ""
        if pd.isna(p.level) or p.function is None:
            reason = "title could not be parsed"
        elif p.level == 1:
            reason = "top of company"
        elif p.level == 2:
            if len(ceos) == 1:
                pred, conf = ceos[0][0], BASE
            else:
                reason = f"{len(ceos)} CEOs found"
        else:
            penalty = 0.0
            for lvl in [x for x in MANAGER_LEVELS if x < p.level]:
                pool = index.get((p.function, lvl), [])
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
    df[["level", "function", "team"]] = pd.DataFrame(parsed.tolist(), index=df.index)
    df["team"] = df["team"].astype(object).where(df["team"].notna(), None)

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
