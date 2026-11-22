"""
generate_pdl_people.py  -  builds a true org chart of generated people for real companies
(data/pdl/company_shortlist.csv), then makes a messy copy. No API calls.

Each company has its own random generator (seeded by its domain), so adding companies later
never changes the people of the companies already generated.

  people:  10 departments, CEO to intern. Small functions have a "Head of" instead of a Chief.
  messy:   about 12.5% of titles (abbreviations, typos, mixed case, extra words, vague, blank).
  hard cases, as in generate_data.py: teams with two managers, managers missing from the data,
           contractors, analysts embedded in a business team (dotted line).
  history: about 30% of people have one past job: a job change (another company in the data)
           or a title change (same company). Interns have none.
Departments use only the names infer.py knows: any other name switches on its UK-unit logic.
"""
import random
import re
from datetime import date, timedelta

import pandas as pd

import generate_data as gd
from db import display

TODAY = date(2026, 11, 22)
PROTECTED = {"N001", "N002"}          # stable companies for the app tests: clean leadership
FUNCS = ["Engineering", "Product", "Data", "Security", "Sales", "Marketing", "Customer Success",
         "Finance", "People", "Operations"]
WEIGHTS = {"Software": [.34, .09, .06, .04, .14, .07, .10, .05, .05, .06],
           "Financial services": [.15, .06, .09, .07, .18, .06, .13, .13, .05, .08],
           "Healthcare": [.06, .04, .05, .03, .09, .05, .15, .08, .09, .36],
           "Retail / e-commerce": [.08, .06, .04, .02, .30, .10, .13, .06, .06, .15],
           "IT consulting": [.42, .05, .09, .07, .11, .04, .08, .04, .05, .05]}
GROUP = {"computer software": "Software", "internet": "Software",
         "financial services": "Financial services", "banking": "Financial services",
         "hospital & health care": "Healthcare", "medical practice": "Healthcare",
         "medical devices": "Healthcare", "pharmaceuticals": "Healthcare",
         "retail": "Retail / e-commerce", "information technology and services": "IT consulting"}
COUNTRY = {"australia": "Australia", "india": "India", "united kingdom": "United Kingdom",
           "united states": "United States"}
QUALIFIERS = ["Core", "Growth", "North", "South", "East", "West", "Enterprise", "SMB", "Global",
              "Regional", "Delivery", "Strategy", "Services", "Partners", "Digital", "Central"]
NAMES = {
    "india": (["Aarav", "Priya", "Rohan", "Ananya", "Arjun", "Kabir", "Ishaan", "Neha", "Raj",
               "Nikhil", "Divya", "Pooja", "Anjali", "Kiran", "Vikram", "Meera", "Sanjay",
               "Lakshmi", "Aditya", "Sneha", "Rahul", "Kavya", "Harsh", "Nisha", "Varun", "Tanvi"],
              ["Sharma", "Patel", "Singh", "Kumar", "Kaur", "Gupta", "Iyer", "Mehta", "Reddy",
               "Nair", "Bose", "Das", "Joshi", "Rao", "Verma", "Menon", "Chatterjee", "Pillai",
               "Desai", "Agarwal"]),
    "australia": (["Liam", "Olivia", "Noah", "Isla", "Jack", "Mia", "Lachlan", "Matilda", "Hamish",
                   "Bronte", "Oscar", "Ruby", "Charlotte", "Finn", "Grace", "Wei", "Thanh",
                   "Sophie", "Callum", "Zara", "Ethan", "Chloe"],
                  ["Smith", "Nguyen", "Brown", "Wilson", "Taylor", "Williams", "Jones", "Martin",
                   "Walker", "White", "Thompson", "Morrison", "O'Brien", "Chen", "Tran",
                   "Campbell", "Kelly", "Ryan", "Murphy", "Papadopoulos"]),
    "united kingdom": (["Oliver", "Amelia", "George", "Isla", "Harry", "Freya", "Callum",
                        "Siobhan", "Mohammed", "Fatima", "Thomas", "Emily", "James", "Poppy",
                        "William", "Sophie", "Amara", "Kofi", "Hannah", "Daniel", "Aisha", "Rhys"],
                       ["Smith", "Jones", "Taylor", "Brown", "Davies", "Evans", "Wilson",
                        "Roberts", "Walker", "Khan", "Ali", "Ahmed", "Hughes", "Fitzgerald",
                        "Mensah", "Okafor", "Patel", "McDonald", "Clarke", "Wright"]),
    "united states": (["James", "Maria", "Michael", "Jennifer", "David", "Sarah", "Mateo",
                       "Ximena", "Diego", "Elena", "Ethan", "Ava", "Lucas", "Emma", "Benjamin",
                       "Mei", "Jun", "Omar", "Leila", "Tyler", "Hannah", "Andre"],
                      ["Smith", "Johnson", "Williams", "Garcia", "Rodriguez", "Martinez", "Lee",
                       "Kim", "Nguyen", "Brown", "Davis", "Miller", "Wilson", "Anderson", "Thomas",
                       "Jackson", "Park", "Hernandez", "Cohen", "Washington"])}
VAGUE = ["Ninja", "Rockstar", "Guru", "Team Member", "Staff", "Associate", "Consultant",
         "Evangelist", "Wizard", "Jack of All Trades", "Problem Solver", "Specialist", "Employee"]
TYPOS = [("Manager", "Manger"), ("Engineer", "Enginer"), ("Director", "Direcotr"),
         ("Senior", "Senoir"), ("Marketing", "Marketting"), ("Analyst", "Anaylst"),
         ("Operations", "Opertions"), ("Finance", "Finanace"), ("Product", "Prodcut"),
         ("Executive", "Exectuive"), ("Software", "Sofware")]
ABBREVIATIONS = [("Senior ", "Sr. "), ("Software Engineer", "SWE"), ("Engineering", "Eng"),
                 ("Manager", "Mgr"), ("Marketing", "Mktg"), ("Operations", "Ops"),
                 ("Director of ", "Dir. "), ("Vice President", "VP"), ("Account Executive", "AE"),
                 ("Junior ", "Jr. "), ("Customer Success", "CS"), ("Human Resources", "HR")]
EXTRA_WORDS = ["{t} (Remote)", "{t} | Hiring!", "Passionate {t}", "{t} at {c}",
               "{t} & Team Player", "Acting {t}", "{t} - Part Time", "Interim {t}"]

# ---------------------------------------------------------------- company name capitals
SMALL = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with"}
ACRONYMS = {"llc", "llp", "plc", "uk", "usa", "it", "hr", "ai", "bpo", "ibm", "au", "nz", "anz",
            "emt", "ir", "ost", "af1", "atz", "idbs", "eb", "a2z", "rma", "mbi", "kda", "qa",
            "eds", "cci", "scci", "ibfx", "dai", "tv", "gp", "rv", "kpmg", "hcl", "dxc", "ey",
            "pwc", "tcs", "hsbc", "bnp", "ubs", "abc", "nsw", "vic", "qld"}
NOT_ACRONYMS = {"ltd", "pvt", "pty", "st", "dr", "mt", "rd", "mr", "mrs", "jr", "sr", "www"}
SPECIAL_NAMES = {"whsmith": "WHSmith", "eyeq": "EyeQ", "usergems": "UserGems",
                 "growthops": "GrowthOps", "linkedin": "LinkedIn"}
WEB_ENDINGS = {"com", "net", "org", "io", "co", "au", "uk", "in", "ai"}
WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")


def nice_name(text):
    """PDL's lowercase company name -> readable capitals ('rma portland' -> 'RMA Portland',
    'whsmith australia' -> 'WHSmith Australia', 'würfel' -> 'Würfel'). Text that already has
    capitals is left as it is."""
    if not isinstance(text, str) or text != text.lower():
        return text
    first = True

    def fix(m):
        nonlocal first
        w, before, after = m.group(0), text[:m.start()], text[m.end():]
        was_first, first = first, False
        if w == "www" or before.endswith("www.") or (before.endswith(".") and w in WEB_ENDINGS):
            return w                                   # part of a web address
        if w in SPECIAL_NAMES:
            return SPECIAL_NAMES[w]
        if w in ACRONYMS:
            return w.upper()
        if before.endswith("(") and after.startswith(")") and len(w) <= 4:
            return w.upper()                           # "(ost)", "(p)"
        if len(w) == 1 and w.isalpha() and w not in SMALL:
            return w.upper()
        if w not in NOT_ACRONYMS and 2 <= len(w) <= 6 and w.isascii() \
                and not re.search(r"[aeiouy]", w):
            return w.upper()                           # no vowels: "ncc", "kjr"
        if w in SMALL and not was_first:
            return w
        if re.match(r"[od]['’]\w", w):                 # o'brien
            return w[0].upper() + w[1] + w[2].upper() + w[3:]
        if re.match(r"mc[a-z]{3,}", w):                # mcdonald
            return "Mc" + w[2].upper() + w[3:]
        return w[0].upper() + w[1:]
    return WORD.sub(fix, text)


# ---------------------------------------------------------------- titles
def messy(title, company, rng):
    """(messy title, kind)."""
    r = rng.random()
    if r < .12:
        return None, "blank"
    if r < .24:
        return rng.choice(VAGUE), "vague"
    if r < .50:
        hits = [(a, b) for a, b in ABBREVIATIONS if a in title]
        if hits:
            t = title
            for a, b in rng.sample(hits, min(2, len(hits))):
                t = t.replace(a, b)
            return (t.lower().replace(", ", " - ") if rng.random() < .35 else t), "abbreviation"
    if r < .68:
        hits = [(a, b) for a, b in TYPOS if a in title]
        if hits:
            a, b = rng.choice(hits)
            return title.replace(a, b), "typo"
    if r < .84:
        k = rng.random()
        if k < .4:
            return title.upper(), "mixed case"
        if k < .8:
            return title.lower(), "mixed case"
        return "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(title)), "mixed case"
    return rng.choice(EXTRA_WORDS).format(t=title, c=company), "extra words"


def ic_title(func, level, team, rng):
    if level == 8 and rng.random() < .35:
        return f"{gd.FUNCTIONS[func][4]} Intern, {team}", "Intern"
    t, _ = gd.real_world_title(func, level, team)
    return t, "Intern" if " Intern," in t else {6: "Senior", 7: "Mid", 8: "Junior"}[level]


def split(n, weights):
    raw = [n * w / sum(weights) for w in weights]
    out = [max(3, int(x)) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - int(raw[i]), reverse=True)
    i = 0
    while sum(out) < n:
        out[order[i % len(out)]] += 1
        i += 1
    while sum(out) > n:
        out[max(range(len(out)), key=lambda x: out[x])] -= 1
    return out


# ---------------------------------------------------------------- one company
def build_company(co):
    """co: company_id, name, domain, industry, size, city, country, planned_people.
    Returns one dict per true employee (in_dataset = False: missing from the data)."""
    rng = random.Random(f"org-{co['domain']}")
    gd.rng = rng                       # generate_data's title helpers draw from this
    cid, name, protect = co["company_id"], nice_name(co["name"]), co["company_id"] in PROTECTED
    city, country = nice_name(co["city"]), co["country"]
    firsts, lasts = NAMES[country]
    people = []

    def person():
        f, l = rng.choice(firsts), rng.choice(lasts)
        return f"{f} {rng.choice('ABCDEGHJKLMNPRST')}. {l}" if rng.random() < .08 else f"{f} {l}"

    def add(func, level, team, manager, title, seniority):
        pid = f"{cid}-E{len(people) + 1:04d}"
        people.append(dict(
            company_id=cid, company_name=name, industry=display.proper_title(co["industry"]),
            size=co["size"], country=COUNTRY[country], employee_id=pid, full_name=person(),
            location=city if rng.random() < .75 else rng.choice([city, COUNTRY[country]]),
            department=gd.FUNCTIONS[func][0] if func != "Executive" else "Executive",
            true_department=gd.FUNCTIONS[func][0] if func != "Executive" else "Executive",
            true_function=func, true_level=level, true_team=team, clean_title=title, title=title,
            seniority=seniority, true_manager_id=manager, employment_type="employee",
            in_dataset=True, dotted_manager_id=None, junk_kind=None, shared=False))
        return pid

    ceo = add("Executive", 1, None, None, gd.real_world_title("Engineering", 1, None)[0], "CEO")
    sizes = split(co["planned_people"] - 1, WEIGHTS[GROUP[co["industry"]]])
    for func, hc in zip(FUNCS, sizes):
        area, teams = gd.FUNCTIONS[func][4], gd.FUNCTIONS[func][3]
        if protect:
            head_title, head_level, head_kind = gd.FUNCTIONS[func][1], 2, "C-level"
        elif co["planned_people"] < 500 and hc < 25:        # small team: no Chief
            head_title, head_level, head_kind = f"Head of {area}", 4, "Head of"
        else:
            head_title, head_level, head_kind = gd.real_world_title(func, 2, None)[0], 2, "C-level"
        head = add(func, head_level, None, ceo, head_title, head_kind)
        left = hc - 1
        tops = [head]
        if hc >= 60:
            tops = []
            for _ in range(max(1, round(hc / 350))):
                t = gd.real_world_title(func, 3, None)[0]
                tops.append(add(func, 3, None, head, t, "Head of" if t.startswith("Head of")
                                else "VP"))
                left -= 1
        n_managers = max(1, round(hc / 9))
        bases = teams[:max(1, min(len(teams), n_managers))]
        directors = {}
        if hc >= 25:
            for i, b in enumerate(bases):
                if rng.random() < .25:
                    t, kind = f"Head of {area}, {b}", "Head of"
                else:
                    t = gd.real_world_title(func, 4, b)[0]
                    kind = "Head of" if t.startswith("Head of") else "Director"
                directors[b] = add(func, 4, b, tops[i % len(tops)], t, kind)
                left -= 1
        managers = []
        for i in range(n_managers):
            b, q = bases[i % len(bases)], i // len(bases)
            team = b if q == 0 else f"{b} {QUALIFIERS[(q - 1) % len(QUALIFIERS)]}"
            t = f"Group Product Manager, {team}" if func == "Product" \
                else gd.real_world_title(func, 5, team)[0]
            managers.append((add(func, 5, team, directors.get(b, tops[0]), t, "Manager"), team))
            left -= 1
        for i in range(max(0, left)):
            mgr, team = managers[i % len(managers)]
            r = rng.random()
            level = 6 if r < .27 else 7 if r < .78 else 8
            t, kind = ic_title(func, level, team, rng)
            add(func, level, team, mgr, t, kind)
            if rng.random() < gd.CONTRACTOR_P:
                people[-1]["employment_type"] = "contractor"
                people[-1]["title"] = rng.choice(gd.CONTRACT_FORMATS).format(t=t)

    by_id = {p["employee_id"]: p for p in people}
    teams = {}
    for p in people:
        if p["true_level"] == 5:
            teams.setdefault((p["true_function"], p["true_team"]), []).append(p)
    for managers in teams.values():
        kids = [p for p in people if p["true_manager_id"] == managers[0]["employee_id"]]
        if len(kids) >= 8 and rng.random() < gd.SHARED_TEAM_P and not protect:
            second, rest = kids[0], kids[1:]       # a team member becomes the second manager
            second.update(true_level=5, seniority="Manager", title=managers[0]["title"],
                          clean_title=managers[0]["title"], employment_type="employee",
                          true_manager_id=managers[0]["true_manager_id"], shared=True)
            for i, k in enumerate(rest):
                if i % 2:
                    k["true_manager_id"] = second["employee_id"]
            managers[0]["shared"] = True
    can_miss = len(people) - 100                   # at least 100 visible people per company
    for p in people:
        if can_miss > 0 and p["true_level"] in (3, 4, 5) and not protect \
                and rng.random() < gd.MISSING_MGR_P:
            p["in_dataset"] = False
            can_miss -= 1
    business = [p for p in people if p["true_level"] == 5
                and p["true_function"] in ("Marketing", "Sales", "Finance")]
    for p in people:
        if business and p["true_function"] == "Data" and p["true_level"] >= 6 \
                and str(p["true_team"]).startswith("Analytics") and rng.random() < gd.EMBEDDED_P:
            m = rng.choice(business)
            p["dotted_manager_id"], p["department"] = m["employee_id"], m["department"]
            p["title"] = str(p["title"]).replace("Analytics", str(m["true_team"]))
    for p in people:
        boss = by_id.get(p["true_manager_id"])
        p["scenario"] = ("manager_missing" if boss and not boss["in_dataset"] else
                         "shared_team" if boss and boss["shared"] else
                         "dotted_line" if p["dotted_manager_id"] else
                         "contractor" if p["employment_type"] == "contractor" else "normal")
        if not (protect and p["true_level"] <= 5) and rng.random() < .125:
            p["title"], p["junk_kind"] = messy(p["title"], name, rng)
        if rng.random() < .22 and not (protect and p["true_level"] <= 2):
            p["department"] = None
        if rng.random() < .05:
            p["location"] = None
    return people


# ---------------------------------------------------------------- past jobs
def past_title(p, kind, rng):
    """One step below today's job (a job change may be sideways). A title change always
    has a different title."""
    level, func, team = p["true_level"], p["true_function"], p["true_team"]
    if func == "Executive":
        return "Chief Operating Officer"
    if not isinstance(team, str) or not team:
        team = gd.FUNCTIONS[func][3][0]
    if level == 8:
        return f"{gd.FUNCTIONS[func][4]} Intern, {team}"
    low = level if kind == "job_change" and rng.random() < .25 else level + 1
    t = gd.clean_title(func, low, team)
    if kind == "title_change" and t.lower() == str(p["clean_title"]).lower():
        t = gd.clean_title(func, min(8, low + 1), team)
    if kind == "title_change" and t.lower() == str(p["clean_title"]).lower():
        t = f"Trainee {gd.FUNCTIONS[func][5]}, {team}"
    return t


def past_jobs(people, companies):
    """people: visible people (dicts). companies: all companies a past job may be at
    (company_id, year_founded, city, country). Returns (start dates {person: date}, past jobs)."""
    founded = {c["company_id"]: int(c["year_founded"]) for c in companies}
    city = {c["company_id"]: nice_name(c["city"]) for c in companies}
    by_country, every = {}, [c["company_id"] for c in companies]
    for c in companies:
        by_country.setdefault(COUNTRY[c["country"]], []).append(c["company_id"])
    starts, jobs = {}, []
    latest = TODAY - timedelta(days=30)
    for p in people:
        rng = random.Random(f"past-{p['employee_id']}")
        cid = p["company_id"]
        low = date(max(2005, founded[cid]), 1, 1)
        first = max(low, date(2016, 1, 1)) if low < latest else latest - timedelta(days=20)
        start = first + timedelta(days=rng.randint(0, max(0, (latest - first).days)))
        starts[p["employee_id"]] = start
        if p["seniority"] == "Intern" or rng.random() >= .32:
            continue
        kind, other = ("title_change", cid) if rng.random() < .45 else ("job_change", cid)
        if kind == "job_change":
            pool = by_country[p["country"]] if rng.random() < .85 else every
            for _ in range(8):
                other = rng.choice(pool)
                if other != cid and founded[other] <= start.year - 1:
                    break
            else:
                kind, other = "title_change", cid
        end = start - timedelta(days=1 if kind == "title_change" else rng.randint(1, 60))
        floor = date(max(1990, founded[other]), 1, 1)
        if end <= floor:                           # the company is too new for an earlier job
            kind, other = "title_change", cid
            floor = date(max(1990, founded[cid]), 1, 1)
            start = min(floor + timedelta(days=200), latest)
            starts[p["employee_id"]] = start
            end = start - timedelta(days=1)
        begin = max(floor, end - timedelta(days=rng.randint(240, 1700)))
        jobs.append(dict(employee_id=p["employee_id"], past_company_id=other,
                         past_title=past_title(p, kind, rng), department=p["true_department"],
                         location=city[other] if kind == "job_change" else p["location"],
                         start_date=begin, end_date=end, change_type=kind, current_start=start))
    return starts, jobs


def generate(new_companies, all_companies):
    """new_companies: dicts to generate. all_companies: every company (past jobs may be at any).
    Returns (true employees, visible people with start_date and change_type, past jobs)."""
    rows = []
    for co in new_companies:
        rows.extend(build_company(co))
    truth = pd.DataFrame(rows)
    visible = [r for r in rows if r["in_dataset"]]
    starts, jobs = past_jobs(visible, all_companies)
    jobs = pd.DataFrame(jobs)
    live = truth[truth.in_dataset].copy()
    live["start_date"] = live.employee_id.map(starts)
    live["change_type"] = live.employee_id.map(jobs.set_index("employee_id").change_type) \
        .fillna("initial")
    return truth, live, jobs
