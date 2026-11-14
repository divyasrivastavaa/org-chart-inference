"""Write data/real/truth.csv: one row per person in data/real/employees.csv.

true_manager_id is filled ONLY when a public company source explicitly says who the
person reports to ("reporting to ...", "will report to ..."). Everyone else is left
blank = unknown (NOT "has no manager"). Each confirmed line keeps the exact quote,
the source URL and the source date, so it can be re-checked.
Run after build_real.py:  python scripts/build_real_truth.py
"""
import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "real"

# (company_id, person, manager, source_date, source_url, quote)
LINES = [
    ("R01", "Rodrigo Castillo", "Matt Comyn", "2025-11-24",
     "https://www.commbank.com.au/articles/newsroom/2025/11/gavin-munroe-announcement.html",
     "will step in and co-lead the Technology function, reporting to CEO Matt Comyn"),
    ("R01", "Victoria Ledda", "Matt Comyn", "2025-11-24",
     "https://www.commbank.com.au/articles/newsroom/2025/11/gavin-munroe-announcement.html",
     "will step in and co-lead the Technology function, reporting to CEO Matt Comyn"),
    ("R02", "Kate Dee", "Anthony Miller", "2025-04-02",
     "https://www.westpac.com.au/about-westpac/media/media-releases/2025/2-april/",
     "reporting directly to CEO Anthony Miller"),
    ("R02", "Dr Andrew McMullan", "Anthony Miller", "2025-05-26",
     "https://www.westpac.com.au/about-westpac/media/media-releases/2025/26-may/",
     "reporting to CEO Anthony Miller"),
    ("R03", "Pete Steel", "Andrew Irvine", "2025-06-24",
     "https://www.nab.com.au/news/nab-updates/nab-appoints-group-executive",
     "In this new role reporting to NAB Group Chief Executive Officer, Andrew Irvine"),
    ("R03", "Shane Conway", "Andrew Irvine", "2025-10-21",
     "https://www.nab.com.au/news/nab-updates/nab-announces-executive-changes",
     "Shane Conway has been promoted to Group Executive, Transformation, reporting to Mr Irvine, effective 1 December."),
    ("R04", "Pedro Rodeia", "Nuno Matos", "2025-10-09",
     "https://www.anz.com.au/newsroom/media/2025/october/appointments-to-anz-group-executive-committee/",
     "three new senior leadership appointments to the bank's Group Executive Committee, reporting to Chief Executive Officer Nuno Matos"),
    ("R04", "Christine Palmer", "Nuno Matos", "2025-10-09",
     "https://www.anz.com.au/newsroom/media/2025/october/appointments-to-anz-group-executive-committee/",
     "three new senior leadership appointments to the bank's Group Executive Committee, reporting to Chief Executive Officer Nuno Matos"),
    ("R04", "Donald Patra", "Nuno Matos", "2025-10-09",
     "https://www.anz.com.au/newsroom/media/2025/october/appointments-to-anz-group-executive-committee/",
     "three new senior leadership appointments to the bank's Group Executive Committee, reporting to Chief Executive Officer Nuno Matos"),
    ("R09", "Annette Karantoni", "Amanda Bardwell", "2025-02-04",
     "https://www.woolworthsgroup.com.au/content/dam/wwg/investors/asx-announcements/2025/2845180.pdf",
     "Annette Karantoni has been appointed to the role of Managing Director, Woolworths Retail, reporting to Amanda Bardwell"),
    ("R16", "Lyndal York", "Lewis Gradon", "2018-10-23",
     "https://www.fphcare.com/en-gb/corporate/investor/news/fy19/fph-appointment-of-new-cfo/",
     "Mrs York will join the company in March 2019, reporting to Chief Executive Officer, Lewis Gradon."),
    ("R18", "Jonathan Meltzer", "Maninder Sawhney", "2026-07-27",
     "https://www.xero.com/us/media-releases/us-managing-director-announcement/",
     "He will report to Maninder Sawhney, who will join Xero as Chief Business Officer in August"),
]

with open(DATA / "employees.csv", encoding="utf-8") as f:
    people = list(csv.DictReader(f))
by_name = {(p["company_id"], p["full_name"]): p["employee_id"] for p in people}

confirmed = {}
for cid, person, manager, date, url, quote in LINES:
    # Fails loudly if a name doesn't match employees.csv exactly
    emp, mgr = by_name[(cid, person)], by_name[(cid, manager)]
    confirmed[emp] = (mgr, quote, url, date)

cols = ["company_id", "company_name", "employee_id", "full_name", "title",
        "true_manager_id", "evidence_quote", "source_url", "source_date"]
with open(DATA / "truth.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(cols)
    for p in people:
        mgr, quote, url, date = confirmed.get(p["employee_id"], ("", "", "", ""))
        w.writerow([p["company_id"], p["company_name"], p["employee_id"], p["full_name"],
                    p["title"], mgr, quote, url, date])
print(f"{len(people)} people, {len(confirmed)} with a confirmed manager")
