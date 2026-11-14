"""Write data/real/truth.csv: one row per person in data/real/employees.csv.

true_manager_id is filled ONLY when a public company source says in writing who the
person reports to. Everyone else is left blank = unknown (NOT "has no manager").
Each line keeps the exact quote, source URL and source date, so it can be re-checked.

evidence_type:
  explicit        a source about this person ("X ... reporting to CEO Y")
  team_statement  a company-level statement in an annual report / governance statement
                  that a defined group reports to the CEO (applied ONLY to people that
                  group clearly covers). An explicit source always wins over a team one.
Run after build_real.py:  python scripts/build_real_truth.py
"""
import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "real"

# ---- explicit: (company_id, person, manager, source_date, source_url, quote)
LINES = [
    ("R01", "Rodrigo Castillo", "Matt Comyn", "2026-06-19",
     "https://www.commbank.com.au/articles/newsroom/2026/06/elt-update.html",
     "Victoria and Rodrigo will report directly to the CEO and join CBA's Executive Leadership Team."),
    ("R01", "Victoria Ledda", "Matt Comyn", "2026-06-19",
     "https://www.commbank.com.au/articles/newsroom/2026/06/elt-update.html",
     "Victoria and Rodrigo will report directly to the CEO and join CBA's Executive Leadership Team."),
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

# ---- team_statement: (company_id, ceo, [members it covers], source_date, url, quote)
# Members are listed by name on purpose: only people the statement clearly covers.
TEAM_STATEMENTS = [
    # "Executive Team" is exactly the name of the page the R02 list came from
    ("R02", "Anthony Miller",
     ["Kate Dee", "Shannon Finch", "Paul Fowler", "Nathan Goonan", "Richard Heeley",
      "Peter Herbert", "Nell Hutton", "Carolyn McCann", "Catherine McGrath",
      "Dr Andrew McMullan", "Fiona Wild", "Ryan Zanin"], "2026",
     "https://www.westpac.com.au/content/dam/public/wbc/documents/pdf/aw/ic/wbc-corporate-governance-statement.pdf",
     "Executive Team is defined as the CEO and CEO Direct Reports, excluding support staff. "
     "(2026 Corporate Governance Statement, p.14)"),
    # Applies to "Group Executives" only -> people whose title says Group Executive
    ("R01", "Matt Comyn",
     ["Monique Macleod", "Stuart Munro", "Karen O'Flynn", "Angus Sullivan",
      "Sinead Taylor", "Michael Vacy-Lyle"], "2025",
     "https://www.commbank.com.au/content/dam/commbank-assets/investors/docs/results/fy25/corporate-governance-statement-fy25.pdf",
     "Throughout this Statement, 'CEO direct reports' refers to all Group Executives and excludes "
     "those direct reports of the CEO who are not Group Executives. (FY25 Corporate Governance Statement, p.5)"),
    # Old (2021) definition; applies to titles that say Group Executive
    ("R03", "Andrew Irvine",
     ["Andrew Auerbach", "Jon Benson", "Cathryn Carver", "Ana Marinkovic",
      "Inder Singh", "Sarah White", "Pete Steel", "Shane Conway"], "2021",
     "https://www.nab.com.au/content/dam/nab/documents/reports/corporate/2021-corporate-governance-statement.pdf",
     "Group Executives describes the executives who report to the Group CEO. "
     "(2021 Corporate Governance Statement, p.26)"),
    # Role-level statement about the CRO only
    ("R05", "Greg Ward", ["Andrew Cassidy"], "2026",
     "https://www.macquarie.com/assets/macq/about/company/corporate-governance-documents/macquarie-group-fy26-corporate-governance-summary.pdf",
     "The Head of RMG, as Macquarie's CRO, reports directly to the CEO and is a member of the "
     "Executive Committee. (2026 Corporate Governance Statement, p.15)"),
    # Annual report says "Executive Leadership Team"; the R07 page is titled "Executive Team"
    ("R07", "Iñaki Berroeta",
     ["John Boniciolli", "Giovanni Chiarelli", "Trent Czinner", "Vanessa Hicks",
      "Jonathan Rutherford", "James Gully"], "2025-03",
     "https://www.tpgtelecom.com.au/sites/default/files/2025-03/TPG-Telecom-2024-AnnualReport-FINAL.pdf",
     "The Executive Leadership Team (ELT) is made up of the CEO and executive direct reports of "
     "the CEO (Annual Report 2024, p.20)"),
    # Covers only the two roles the statement names
    ("R20", "Stuart Irving", ["Nick Oldfield", "Dominic Horsley"], "2025-09-22",
     "https://content-assets.computershare.com/eh96rkuu9740/7o8XHlWTAAprJgmcclpwj9/f9a0eb21ff1610f46b01d6324dd93441/CPU_-_Corporate_Governance_Statement_-_FY25.pdf",
     "'Group management' refers to the Group's Chief Executive Officer and the executives reporting "
     "directly to the Chief Executive Officer ... Group management personnel, including the Chief "
     "Financial Officer and Company Secretary. (FY25 Corporate Governance Statement, p.2)"),
]

with open(DATA / "employees.csv", encoding="utf-8") as f:
    people = list(csv.DictReader(f))
# Only public people can get an answer. Manually added people (source = "manually
# added") have no written source, so they stay blank and are never scored.
by_name = {(p["company_id"], p["full_name"]): p["employee_id"] for p in people
           if p.get("source", "public") == "public"}

confirmed = {}
for cid, person, manager, date, url, quote in LINES:
    # Fails loudly if a name doesn't match employees.csv exactly
    emp, mgr = by_name[(cid, person)], by_name[(cid, manager)]
    confirmed[emp] = (mgr, "explicit", quote, url, date)
for cid, ceo, members, date, url, quote in TEAM_STATEMENTS:
    mgr = by_name[(cid, ceo)]
    for person in members:
        emp = by_name[(cid, person)]
        confirmed.setdefault(emp, (mgr, "team_statement", quote, url, date))  # explicit wins

cols = ["company_id", "company_name", "employee_id", "full_name", "title", "source",
        "true_manager_id", "evidence_type", "evidence_quote", "source_url", "source_date"]
with open(DATA / "truth.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(cols)
    for p in people:
        mgr, kind, quote, url, date = confirmed.get(p["employee_id"], ("", "", "", "", ""))
        w.writerow([p["company_id"], p["company_name"], p["employee_id"], p["full_name"],
                    p["title"], p.get("source", "public"), mgr, kind, quote, url, date])
kinds = [v[1] for v in confirmed.values()]
print(f"{len(people)} people: {kinds.count('explicit')} explicit, "
      f"{kinds.count('team_statement')} team_statement, {len(people) - len(confirmed)} unknown")
