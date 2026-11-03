"""
generate_data.py  -  Steps 2 and 3
Builds a synthetic dataset: the TRUE org chart first, then a messy flat copy.

Outputs (in data/):
  employees.csv -> what the inference code is allowed to see (no manager column, messy)
  truth.csv     -> the hidden answer key (true manager, clean title, level, function)
"""
import random
from pathlib import Path

import pandas as pd

SEED = 42
random.seed(SEED)
N_COMPANIES = 500
OUT = Path("data")
OUT.mkdir(exist_ok=True)

FIRST = ["Aarav", "Priya", "Liam", "Olivia", "Noah", "Emma", "Rohan", "Ananya", "Jack", "Mia",
         "Wei", "Mei", "Arjun", "Sara", "Lucas", "Chloe", "Ethan", "Zara", "Kabir", "Isla",
         "Oscar", "Ava", "Dev", "Ruby", "Leo", "Grace", "Ishaan", "Lily", "Hugo", "Neha"]
LAST = ["Sharma", "Smith", "Nguyen", "Patel", "Brown", "Wilson", "Chen", "Taylor", "Singh",
        "Kumar", "Williams", "Jones", "Lee", "Martin", "Kaur", "Walker", "Gupta", "Khan",
        "White", "Thompson", "Roberts", "Iyer", "Davies", "Wang", "Hall", "Mehta"]
WORDS = ["Nova", "Blue", "Peak", "Orbit", "Kite", "Harbour", "Quartz", "Summit", "Atlas",
         "Lumen", "Cedar", "Pulse", "Vertex", "Echo", "Maple", "Iron", "Coral", "Zen"]
SUFFIX = ["Labs", "Systems", "Group", "Health", "Logistics", "Pty Ltd", "Technologies", "Co"]
INDUSTRIES = ["SaaS", "Fintech", "Healthcare", "Retail", "Manufacturing",
              "Logistics", "Education", "Media"]
LOCATIONS = ["Sydney", "Melbourne", "Brisbane", "Bengaluru", "London", "Singapore"]
SIZE_BANDS = [(100, 200, 0.55), (201, 500, 0.30), (501, 1200, 0.15)]

# function -> (department, head title, share of headcount, teams, area word, IC title)
FUNCTIONS = {
    "Engineering": ("Engineering", "Chief Technology Officer", 0.28,
                    ["Platform", "Payments", "Mobile", "Infrastructure", "Web"],
                    "Engineering", "Software Engineer"),
    "Sales": ("Sales", "Chief Revenue Officer", 0.18,
              ["Enterprise", "Mid-Market", "APAC", "EMEA"], "Sales", "Account Executive"),
    "Marketing": ("Marketing", "Chief Marketing Officer", 0.08,
                  ["Growth", "Brand", "Content"], "Marketing", "Marketing Specialist"),
    "Finance": ("Finance", "Chief Financial Officer", 0.07,
                ["FP&A", "Accounting", "Payroll"], "Finance", "Financial Analyst"),
    "People": ("Human Resources", "Chief People Officer", 0.06,
               ["Talent Acquisition", "HR Operations", "L&D"], "People", "HR Generalist"),
    "Operations": ("Operations", "Chief Operating Officer", 0.12,
                   ["Supply Chain", "Facilities", "Procurement"], "Operations",
                   "Operations Coordinator"),
    "Product": ("Product", "Chief Product Officer", 0.07,
                ["Core", "Checkout", "Apps"], "Product", "Product Manager"),
    "Data": ("Data", "Chief Data Officer", 0.06,
             ["Analytics", "Data Engineering", "Data Science"], "Data", "Data Analyst"),
    "Security": ("Security", "Chief Information Security Officer", 0.04,
                 ["AppSec", "SecOps", "GRC"], "Security", "Security Engineer"),
    "Customer Success": ("Customer Success", "Chief Customer Officer", 0.04,
                         ["Onboarding", "Support", "Account Management"],
                         "Customer Success", "Customer Success Associate"),
}
# Levels: 1 CEO, 2 C-level head, 3 VP, 4 Director, 5 Manager, 6 Senior IC, 7 IC, 8 Junior


# ---------------------------------------------------------------- Step 2: true org
def pick_size():
    r, acc = random.random(), 0
    for lo, hi, share in SIZE_BANDS:
        acc += share
        if r <= acc:
            return random.randint(lo, hi)
    return random.randint(100, 200)


def clean_title(func, level, team):
    _, head, _, _, area, ic = FUNCTIONS[func]
    if func == "Product" and level == 5:   # "Product Manager" is already the IC title
        return f"Group Product Manager, {team}"
    return {2: head, 3: f"VP of {area}", 4: f"Director of {area}, {team}",
            5: f"{area} Manager, {team}", 6: f"Senior {ic}, {team}",
            7: f"{ic}, {team}", 8: f"Junior {ic}, {team}"}[level]


def build_company(cid, used_names):
    size, industry, hq = pick_size(), random.choice(INDUSTRIES), random.choice(LOCATIONS)
    name = f"{random.choice(WORDS)}{random.choice(WORDS).lower()} {random.choice(SUFFIX)}"
    while name in used_names:
        name += " " + random.choice(["AU", "Global", "Holdings"])
    used_names.add(name)
    people = []

    def add(func, level, team, manager_id):
        pid = f"C{cid:03d}-E{len(people) + 1:04d}"
        people.append(dict(
            company_id=f"C{cid:03d}", company_name=name, industry=industry, size=size,
            employee_id=pid, full_name=f"{random.choice(FIRST)} {random.choice(LAST)}",
            location=hq if random.random() < 0.7 else random.choice(LOCATIONS),
            department=FUNCTIONS[func][0] if func != "Executive" else "Executive",
            true_function=func, true_level=level, true_team=team,
            title="Chief Executive Officer" if level == 1 else clean_title(func, level, team),
            true_manager_id=manager_id))
        return pid

    ceo = add("Executive", 1, None, None)
    funcs = list(FUNCTIONS)
    if size < 200:                         # small companies skip one of these functions
        funcs.remove(random.choice(["Security", "Data"]))
    total_w = sum(FUNCTIONS[f][2] for f in funcs)
    managers = []

    for func in funcs:
        headcount = max(3, round((size - 1) * FUNCTIONS[func][2] / total_w))
        all_teams = FUNCTIONS[func][3]
        teams = all_teams[:max(1, min(len(all_teams), headcount // 8))]
        head = add(func, 2, None, ceo)
        top = add(func, 3, None, head) if headcount > 60 else head   # big function -> VP
        remaining = headcount - (2 if top != head else 1)
        per_team = max(2, remaining // len(teams))
        for team in teams:
            boss = add(func, 4, team, top) if per_team > 20 else top  # big team -> Director
            mgr = add(func, 5, team, boss)
            managers.append((func, team, mgr))
            for _ in range(max(1, per_team - (2 if boss != top else 1))):
                r = random.random()
                add(func, 6 if r < 0.3 else (7 if r < 0.85 else 8), team, mgr)
    while len(people) < max(100, size):    # rounding can leave us short: top up
        func, team, mgr = random.choice(managers)
        add(func, 7, team, mgr)
    return people


# ---------------------------------------------------------------- Step 3: make it messy
ABBREV = [("Chief Executive Officer", "CEO"), ("Chief Technology Officer", "CTO"),
          ("Chief Financial Officer", "CFO"), ("Chief Operating Officer", "COO"),
          ("Chief Marketing Officer", "CMO"), ("Chief Information Security Officer", "CISO"),
          ("Chief Revenue Officer", "CRO"), ("Chief People Officer", "CPO - People"),
          ("Senior ", "Sr. "), ("Senior ", "Snr "), ("Manager", "Mgr"),
          ("Director of ", "Dir. "), ("Engineering", "Eng"), ("Marketing", "Mktg"),
          ("Vice President", "VP"), ("Software Engineer", "SWE"), ("Junior ", "Jr. "),
          ("Operations", "Ops"), ("Account Executive", "AE"), ("Human Resources", "HR")]
SYNONYM = [("VP of ", "Vice President, "), ("Director of ", "Head of "),
           ("Junior ", "Associate "), ("Software Engineer", "Developer"),
           ("Data Analyst", "Analyst"), ("Senior ", "Lead ")]


def messy_title(title):
    t = title
    for full, short in SYNONYM:            # synonyms first
        if full in t and random.random() < 0.25:
            t = t.replace(full, short)
    for full, short in ABBREV:             # then abbreviations
        if full in t and random.random() < 0.35:
            t = t.replace(full, short)
    r = random.random()
    if r < 0.10:
        t = t.lower()
    elif r < 0.15:
        t = t.upper()
    if random.random() < 0.15 and ", " in t:     # drop the team part
        t = t.split(", ")[0]
    if random.random() < 0.10:
        t = t.replace(", ", " - ")
    if random.random() < 0.05:
        t = "  " + t + " "
    return t


def make_messy(df):
    m = df.copy()
    m["title"] = m["title"].map(messy_title)
    m.loc[m.sample(frac=0.25, random_state=SEED).index, "department"] = None
    m.loc[m.sample(frac=0.02, random_state=SEED + 1).index, "title"] = None
    m.loc[m.sample(frac=0.05, random_state=SEED + 2).index, "location"] = None
    dups = m.sample(frac=0.01, random_state=SEED + 3).copy()   # duplicate rows
    dups["employee_id"] = dups["employee_id"] + "-DUP"
    m = pd.concat([m, dups]).sample(frac=1, random_state=SEED).reset_index(drop=True)
    keep = ["company_id", "company_name", "industry", "size", "employee_id",
            "full_name", "title", "department", "location"]
    return m[keep]


def main():
    used, rows = set(), []
    for cid in range(1, N_COMPANIES + 1):
        rows.extend(build_company(cid, used))
    truth = pd.DataFrame(rows)
    truth.to_csv(OUT / "truth.csv", index=False)
    messy = make_messy(truth)
    messy.to_csv(OUT / "employees.csv", index=False)
    per_co = truth.groupby("company_id").size()
    print(f"{truth.company_id.nunique()} companies, {len(truth)} true employees, "
          f"{len(messy)} rows in employees.csv")
    print(f"smallest company: {per_co.min()} people, largest: {per_co.max()}")


if __name__ == "__main__":
    main()
