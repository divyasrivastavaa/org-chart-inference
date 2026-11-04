"""
generate_data.py  -  Steps 2 and 3
Builds a synthetic dataset: the TRUE org chart first, then a messy flat copy.

Outputs (in data/):
  employees.csv -> what the inference code is allowed to see (no manager column, messy)
  truth.csv     -> the hidden answer key (true manager, clean title, level, function,
                   plus the scenario each person belongs to)

Two versions:
  python generate_data.py        -> v2 (default): realistic, harder data
  python generate_data.py --v1   -> v1: the original dataset, byte-for-byte reproducible

v2 adds, on top of v1:
  * more first/last names and middle initials
  * real-world title variety: "Founder & CEO", "Managing Director", "SVP", "Head of
    Engineering" (a VP!), "Team Lead", "Staff/Principal", "Customer Success Manager"
    (an IC!), "Graduate", "Intern" ...
  * teams with 2 managers (both have the same title)
  * missing middle managers (left / no profile): in truth.csv, not in employees.csv
  * contractors ("(Contractor)", "Freelance ...")
  * dotted lines: data analysts embedded in Marketing/Sales/Finance. Solid line to the
    Data manager, dotted line to the business-team manager. Their title and department
    show the business team, which is the trap.
"""
import random
import sys
from pathlib import Path

import pandas as pd

SEED = 42
N_COMPANIES = 500
OUT = Path("data")
V2 = True                                  # set by main()
rng = random.Random(SEED)                  # private generator, set by main()

FIRST = ["Aarav", "Priya", "Liam", "Olivia", "Noah", "Emma", "Rohan", "Ananya", "Jack", "Mia",
         "Wei", "Mei", "Arjun", "Sara", "Lucas", "Chloe", "Ethan", "Zara", "Kabir", "Isla",
         "Oscar", "Ava", "Dev", "Ruby", "Leo", "Grace", "Ishaan", "Lily", "Hugo", "Neha"]
LAST = ["Sharma", "Smith", "Nguyen", "Patel", "Brown", "Wilson", "Chen", "Taylor", "Singh",
        "Kumar", "Williams", "Jones", "Lee", "Martin", "Kaur", "Walker", "Gupta", "Khan",
        "White", "Thompson", "Roberts", "Iyer", "Davies", "Wang", "Hall", "Mehta"]
MORE_FIRST = ["Siobhan", "Mateo", "Aisha", "Tomasz", "Yuki", "Fatima", "Kofi", "Ingrid",
              "Diego", "Hana", "Raj", "Sophie", "Omar", "Elena", "Jun", "Amara", "Finn",
              "Leila", "Nikhil", "Charlotte", "Mohammed", "Freya", "Thanh", "Benjamin",
              "Ngaio", "Kiran", "Matilda", "Arash", "Lachlan", "Divya", "Hamish", "Sakura",
              "Xavier", "Pooja", "Callum", "Ximena", "Tariq", "Bronte", "Felipe", "Anjali"]
MORE_LAST = ["O'Brien", "Rossi", "Kowalski", "Tanaka", "Haddad", "Mensah", "Johansson",
             "Fernandez", "Park", "Reddy", "Dubois", "Murphy", "Ali", "Popescu", "Tran",
             "Okafor", "Nair", "Fitzgerald", "Van der Berg", "Ahmed", "Costa", "Morrison",
             "Le", "Bose", "McDonald", "Schmidt", "Yildiz", "Papadopoulos", "Ng", "Das"]
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

# ---------------------------------------------------------------- v2: real-world titles
# What people actually write on their profile. Used instead of the clean title sometimes.
CEO_TITLES = ["Founder & CEO", "Co-Founder & CEO", "Managing Director", "CEO & Co-Founder",
              "Group CEO", "President & CEO"]
HEAD_TITLES = {"Engineering": ["Co-Founder & CTO", "Chief Technical Officer"],
               "Sales": ["Chief Commercial Officer", "Chief Sales Officer"],
               "Marketing": ["Chief Growth Officer"],
               "Finance": ["Finance Director"],
               "People": ["Chief Human Resources Officer", "CHRO"],
               "Operations": ["Co-Founder & COO"],
               "Product": ["CPO", "Co-Founder & Chief Product Officer"],
               "Data": ["Chief Data & Analytics Officer"],
               "Security": ["Chief Security Officer"],
               "Customer Success": ["Chief Experience Officer"]}
IC_TITLES = {"Engineering": ["Backend Engineer", "Frontend Engineer", "Full Stack Developer",
                             "QA Engineer", "Software Engineer II"],
             "Sales": ["Account Manager", "Sales Development Representative", "BDR",
                       "Sales Executive"],
             "Marketing": ["Marketing Coordinator", "Content Writer",
                           "Digital Marketing Executive"],
             "Finance": ["Accountant", "Finance Officer"],
             "People": ["Recruiter", "Talent Partner", "HR Advisor"],
             "Operations": ["Operations Analyst", "Logistics Coordinator"],
             "Product": ["Product Owner", "Product Designer"],
             "Data": ["Data Scientist", "Data Engineer", "BI Analyst"],
             "Security": ["Security Analyst", "SOC Analyst"],
             "Customer Success": ["Customer Success Manager", "Support Specialist",
                                  "Customer Support Rep"]}
TECH = {"Engineering", "Data", "Security", "Product"}
CONTRACT_FORMATS = ["{t} (Contractor)", "Contract {t}", "{t} - Contract", "Freelance {t}"]

SHARED_TEAM_P = 0.30      # big team split between two managers with the same title
MISSING_MGR_P = 0.06      # VP / Director / Manager not in the dataset
CONTRACTOR_P = 0.05       # IC is a contractor
EMBEDDED_P = 0.25         # analytics IC embedded in a business team (dotted line)


# ---------------------------------------------------------------- Step 2: true org
def pick_size():
    r, acc = rng.random(), 0
    for lo, hi, share in SIZE_BANDS:
        acc += share
        if r <= acc:
            return rng.randint(lo, hi)
    return rng.randint(100, 200)


def clean_title(func, level, team):
    _, head, _, _, area, ic = FUNCTIONS[func]
    if func == "Product" and level == 5:   # "Product Manager" is already the IC title
        return f"Group Product Manager, {team}"
    return {2: head, 3: f"VP of {area}", 4: f"Director of {area}, {team}",
            5: f"{area} Manager, {team}", 6: f"Senior {ic}, {team}",
            7: f"{ic}, {team}", 8: f"Junior {ic}, {team}"}[level]


def make_name():
    if not V2:
        return f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    first, last = rng.choice(FIRST + MORE_FIRST), rng.choice(LAST + MORE_LAST)
    if rng.random() < 0.10:
        return f"{first} {rng.choice('ABCDEGHJKLMNPRST')}. {last}"
    return f"{first} {last}"


def real_world_title(func, level, team):
    """v2: the title a person actually uses. Returns (title, is_variant)."""
    if level == 1:
        return (rng.choice(CEO_TITLES), True) if rng.random() < 0.5 \
            else ("Chief Executive Officer", False)
    clean = clean_title(func, level, team)
    _, _, _, _, area, ic = FUNCTIONS[func]
    options = {
        2: (0.30, HEAD_TITLES[func]),
        3: (0.30, [f"SVP, {area}", f"Senior Vice President of {area}", f"Head of {area}"]),
        4: (0.20, [f"Senior Director of {area}, {team}",
                   f"Associate Director of {area}, {team}"]),
        5: (0.20 if func != "Product" else 0, [f"Team Lead, {team}", f"{team} Team Lead",
                                               f"{area} Lead, {team}"]),
        6: (0.25, [f"Principal {ic}, {team}", f"{ic} III, {team}"]
            + ([f"Staff {ic}, {team}"] if func in TECH else [])),
        7: (0.30, [f"{t}, {team}" for t in IC_TITLES[func]]),
        8: (0.30, [f"Graduate {ic}, {team}", f"{area} Intern, {team}",
                   f"Trainee {ic}, {team}"]),
    }[level]
    p, choices = options
    if rng.random() < p:
        return rng.choice(choices), True
    return clean, False


def build_company(cid, used_names):
    size, industry, hq = pick_size(), rng.choice(INDUSTRIES), rng.choice(LOCATIONS)
    name = f"{rng.choice(WORDS)}{rng.choice(WORDS).lower()} {rng.choice(SUFFIX)}"
    while name in used_names:
        name += " " + rng.choice(["AU", "Global", "Holdings"])
    used_names.add(name)
    people = []

    def add(func, level, team, manager_id):
        pid = f"C{cid:03d}-E{len(people) + 1:04d}"
        row = dict(
            company_id=f"C{cid:03d}", company_name=name, industry=industry, size=size,
            employee_id=pid, full_name=make_name(),
            location=hq if rng.random() < 0.7 else rng.choice(LOCATIONS),
            department=FUNCTIONS[func][0] if func != "Executive" else "Executive",
            true_function=func, true_level=level, true_team=team,
            title="Chief Executive Officer" if level == 1 else clean_title(func, level, team),
            true_manager_id=manager_id,
            employment_type="employee", title_variant=False, in_dataset=True,
            dotted_manager_id=None)
        if V2:
            row["title"], row["title_variant"] = real_world_title(func, level, team)
            if level >= 6 and rng.random() < CONTRACTOR_P:
                row["employment_type"] = "contractor"
                row["title"] = rng.choice(CONTRACT_FORMATS).format(t=row["title"])
            if level in (3, 4, 5) and rng.random() < MISSING_MGR_P:
                row["in_dataset"] = False          # left the company / no public profile
        people.append(row)
        return pid

    ceo = add("Executive", 1, None, None)
    funcs = list(FUNCTIONS)
    if size < 200:                         # small companies skip one of these functions
        funcs.remove(rng.choice(["Security", "Data"]))
    total_w = sum(FUNCTIONS[f][2] for f in funcs)
    managers = []
    shared = set()                         # managers who share a team with another manager

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
            n_ic = max(1, per_team - (2 if boss != top else 1))
            mgrs = [add(func, 5, team, boss)]
            if V2 and n_ic >= 12 and rng.random() < SHARED_TEAM_P:
                mgrs.append(add(func, 5, team, boss))                # two managers, one team
                shared.update(mgrs)
                n_ic -= 1
            for m in mgrs:
                managers.append((func, team, m))
            for i in range(n_ic):
                r = rng.random()
                add(func, 6 if r < 0.3 else (7 if r < 0.85 else 8), team, mgrs[i % len(mgrs)])
    while len(people) < max(100, size):    # rounding can leave us short: top up
        func, team, mgr = rng.choice(managers)
        add(func, 7, team, mgr)

    if V2:
        embed_analysts(people, managers)
    by_id = {p["employee_id"]: p for p in people}
    for p in people:                       # which hard case does this person fall into?
        boss = by_id.get(p["true_manager_id"])
        p["scenario"] = ("manager_missing" if boss and not boss["in_dataset"] else
                         "shared_team" if p["true_manager_id"] in shared else
                         "dotted_line" if p["dotted_manager_id"] else
                         "contractor" if p["employment_type"] == "contractor" else
                         "title_variant" if p["title_variant"] else "normal")
    return people


def embed_analysts(people, managers):
    """v2: some Analytics ICs sit inside a business team (dotted line to its manager).
    Their title and department show the business team; their solid line stays in Data."""
    targets = [(f, t, m) for f, t, m in managers if f in ("Marketing", "Sales", "Finance")]
    if not targets:
        return
    for p in people:
        if (p["true_function"] == "Data" and p["true_team"] == "Analytics"
                and p["true_level"] >= 6 and rng.random() < EMBEDDED_P):
            func, team, mgr = rng.choice(targets)
            p["dotted_manager_id"] = mgr
            p["department"] = FUNCTIONS[func][0]
            p["title"] = p["title"].replace("Analytics", team)


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
        if full in t and rng.random() < 0.25:
            t = t.replace(full, short)
    for full, short in ABBREV:             # then abbreviations
        if full in t and rng.random() < 0.35:
            t = t.replace(full, short)
    r = rng.random()
    if r < 0.10:
        t = t.lower()
    elif r < 0.15:
        t = t.upper()
    if rng.random() < 0.15 and ", " in t:     # drop the team part
        t = t.split(", ")[0]
    if rng.random() < 0.10:
        t = t.replace(", ", " - ")
    if rng.random() < 0.05:
        t = "  " + t + " "
    return t


def make_messy(df):
    m = df[df.in_dataset].copy()           # missing managers never reach the dataset
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


def main(version=2, seed=SEED, out=OUT):
    global V2, rng
    V2 = version == 2
    rng = random.Random(seed)       # private: other code in the process can't disturb it
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    used, rows = set(), []
    for cid in range(1, N_COMPANIES + 1):
        rows.extend(build_company(cid, used))
    truth = pd.DataFrame(rows)
    truth.to_csv(out / "truth.csv", index=False)
    messy = make_messy(truth)
    messy.to_csv(out / "employees.csv", index=False)
    per_co = truth.groupby("company_id").size()
    print(f"v{version}: {truth.company_id.nunique()} companies, {len(truth)} true employees, "
          f"{len(messy)} rows in employees.csv")
    print(f"smallest company: {per_co.min()} people, largest: {per_co.max()}")
    if V2:
        print("scenarios:", truth.scenario.value_counts().to_dict())


if __name__ == "__main__":
    # --seed N builds a different dataset with the same recipe: a held-out test set
    # --out DIR writes somewhere other than data/ (e.g. data/holdout_seed7)
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else SEED
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else OUT
    main(1 if "--v1" in sys.argv else 2, seed, out)
