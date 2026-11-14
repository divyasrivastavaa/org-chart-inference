"""Write data/real/employees.csv and data/real/sources.csv from leadership lists
collected on 2026-11-14 from each company's own website (or official filings).
Run from anywhere:  python scripts/build_real.py"""
import csv
import sys
from pathlib import Path

ACCESSED = "2026-11-14"
OUT = Path(__file__).resolve().parent.parent / "data" / "real"   # <repo>/data/real

# (company_name, industry, source_url, [(name, title), ...]) - order as on the page
COMPANIES = [
    ("Commonwealth Bank of Australia", "Banking",
     "https://www.commbank.com.au/about-us/our-company/management.html", [
        ("Matt Comyn", "Managing Director and Chief Executive Officer"),
        ("Emma Bunnell", "Chief Operations Officer"),
        ("Rodrigo Castillo", "Group Chief Technology Officer"),
        ("Alan Docherty", "Group Chief Financial Officer"),
        ("Andrew Hinchliff", "Group Chief Risk Officer"),
        ("Victoria Ledda", "Group Chief Information Officer"),
        ("Monique Macleod", "Group Executive Marketing and Corporate Affairs"),
        ("Stuart Munro", "Group Executive, Group Strategy"),
        ("Karen O'Flynn", "Group General Counsel and Group Executive, Legal & Group Secretariat"),
        ("Kiersten Robinson", "Chief People Officer"),
        ("Vittoria Shortt", "ASB Chief Executive"),
        ("Angus Sullivan", "Group Executive, Retail Banking Services"),
        ("Sinead Taylor", "Group Executive, Institutional Banking and Markets"),
        ("Michael Vacy-Lyle", "Group Executive, Business Banking"),
    ]),
    ("Westpac Banking Corporation", "Banking",
     "https://www.westpac.com.au/about-westpac/westpac-group/executive-team/", [
        ("Anthony Miller", "Managing Director and Chief Executive Officer"),
        ("Kate Dee", "Chief People Officer"),
        ("Shannon Finch", "Group General Counsel"),
        ("Paul Fowler", "Chief Executive, Business & Wealth"),
        ("Nathan Goonan", "Chief Financial Officer"),
        ("Richard Heeley", "Group Chief Information Officer"),
        ("Peter Herbert", "Chief UNITE and Customer Operations Officer"),
        ("Nell Hutton", "Chief Executive, Westpac Institutional Bank"),
        ("Carolyn McCann", "Chief Executive, Consumer"),
        ("Catherine McGrath", "Chief Executive Officer, Westpac New Zealand"),
        ("Dr Andrew McMullan", "Chief Data, Digital and AI Officer"),
        ("Fiona Wild", "Chief Corporate Affairs & Sustainability Officer"),
        ("Ryan Zanin", "Chief Risk Officer"),
    ]),
    ("National Australia Bank", "Banking",
     "https://www.nab.com.au/news/media-hub/media-assets", [
        ("Andrew Irvine", "Group Chief Executive Officer"),
        ("Andrew Auerbach", "Group Executive, Business and Private Banking"),
        ("Jon Benson", "Group General Counsel and Group Executive, Legal and Corporate Affairs"),
        ("Cathryn Carver", "Group Executive, Corporate and Institutional Banking"),
        ("Shane Conway", "Group Executive, Transformation & Operations"),
        ("Ana Marinkovic", "Group Executive, Personal Banking"),
        ("Inder Singh", "Group Chief Financial Officer and Group Executive, Strategy"),
        ("Connie Sokaris", "Group Chief Risk Officer"),
        ("Pete Steel", "Group Executive, Technology & Artificial Intelligence"),
        ("Sarah White", "Group Executive, People and Culture"),
    ]),
    ("ANZ Group Holdings", "Banking",
     "https://www.anz.com/shareholder/centre/about/executive-committee/", [
        ("Nuno Matos", "Group Chief Executive Officer"),
        ("Elisa Clements", "Group Chief People Officer"),
        ("Farhan Faruqui", "Group Chief Financial Officer"),
        ("Ben Kelleher", "Group Executive and Chief Executive Officer of New Zealand"),
        ("Tammy Medard", "Group Executive Business & Private Bank"),
        ("Christine Palmer", "Group Chief Risk Officer"),
        ("Donald Patra", "Group Chief Information Officer"),
        ("Pedro Rodeia", "Group Executive Australia Retail"),
        ("Mark Whelan", "Group Executive, Institutional"),
        ("Stephen White", "Group Chief Operations Officer"),
    ]),
    ("Macquarie Group", "Financial Services",
     "https://www.macquarie.com/au/en/about/company/executive-committee.html", [
        ("Greg Ward", "Managing Director and Chief Executive Officer of Macquarie Group Limited"),
        ("Evie Bruce", "Group General Counsel and Head of Legal and Governance Group"),
        ("Andrew Cassidy", "Chief Risk Officer and Head of Risk Management Group"),
        ("Stuart Green", "Managing Director and Chief Executive Officer of Macquarie Bank Limited"),
        ("Frank Kwok", "Chief Financial Officer and Head of Financial Management, People and Engagement"),
        ("Ben Perham", "Head of Banking and Financial Services"),
        ("Michael Silverton", "Head of Macquarie Capital"),
        ("Nicole Sorbara", "Chief Operating Officer and Head of Corporate Operations Group"),
        ("Ben Way", "Head of Macquarie Asset Management"),
        ("Simon Wright", "Head of Commodities and Global Markets"),
    ]),
    ("Telstra Group", "Telecommunications",
     "https://www.telstra.com.au/aboutus/our-company/leadership-team", [
        ("Vicki Brady", "Chief Executive Officer and Managing Director"),
        ("Michael Ackland", "Chief Financial Officer & Group Executive, Strategy & Finance"),
        ("Brad Whitcomb", "Group Executive, Telstra Consumer"),
        ("Oliver Camplin-Warner", "Group Executive, Telstra Enterprise"),
        ("Amanda Hutton", "Group Executive, Telstra Business"),
        ("Steven Worrall", "CEO, Telstra Digital Infrastructure"),
        ("Lyndall Stoyles", "Group General Counsel and Group Executive, Corporate Affairs, Risk and Legal"),
        ("Kim Krogh Andersen", "Group Executive, Network, Product and Technology"),
        ("Dr Kathryn van der Merwe", "Group Executive, People, Culture and Capability"),
    ]),
    ("TPG Telecom", "Telecommunications",
     "https://www.tpgtelecom.com.au/about-us/our-executive-team", [
        ("Iñaki Berroeta", "Chief Executive Officer and Managing Director"),
        ("John Boniciolli", "Group Chief Financial Officer"),
        ("Giovanni Chiarelli", "Chief Technology Officer"),
        ("Trent Czinner", "Group Executive Legal and External Affairs and Company Secretary"),
        ("Vanessa Hicks", "Group Executive Customer and People Experience"),
        ("Jonathan Rutherford", "Group Executive Wholesale, Enterprise and Government"),
        ("James Gully", "Group Executive Consumer (Acting)"),
    ]),
    ("Wesfarmers", "Retail",
     "https://www.wesfarmers.com.au/who-we-are/leadership-team", [
        ("Rob Scott", "Managing Director and Chief Executive Officer, Wesfarmers"),
        ("Anthony Gianotti", "Chief Financial Officer, Wesfarmers"),
        ("Maya vanden Driesen", "Group General Counsel, Wesfarmers"),
        ("Michael Schneider", "Managing Director, Bunnings Group"),
        ("Aleksandra Spaseska", "Managing Director, Kmart Group"),
        ("John Gualtieri", "Managing Director, Officeworks"),
        ("Aaron Hood", "Managing Director, Wesfarmers Chemicals, Energy & Fertilisers"),
        ("Emily Amos", "Managing Director, Wesfarmers Health"),
        ("Naomi Flutter", "Executive General Manager, Corporate Affairs, Wesfarmers"),
        ("Jenny Bryant", "Chief Human Resources Officer, Wesfarmers"),
        ("Michael Britton", "Executive General Manager, Business Development"),
        ("Sheldon Renkema", "Executive General Manager, Company Secretariat"),
        ("Leah Balter", "Executive General Manager, OneDigital"),
    ]),
    ("Woolworths Group", "Retail",
     "https://www.woolworthsgroup.com.au/au/en/who-we-are/our-leadership-team/management-board.html", [
        ("Amanda Bardwell", "Chief Executive Officer and Managing Director"),
        ("Stephen Harrison", "Chief Financial Officer & Chief Value Officer"),
        ("Annette Karantoni", "Managing Director, Woolworths Retail"),
        ("Amitabh Mall", "Managing Director, Group eComX and Chief Digital Officer"),
        ("Sally Copland", "Managing Director, Woolworths New Zealand"),
        ("Rob McCartney", "Managing Director, Woolworths 360"),
        ("Dan Hake", "Managing Director, BIG W"),
        ("Alan Beacham", "Chief Supply Chain Officer and Managing Director, Primary Connect"),
        ("Mike Tyquin", "Managing Director, Cartology"),
        ("Carly Richards", "Chief Risk Officer"),
        ("Simon Lowden", "Chief Officer, Group Affairs, Communications and Sustainability"),
        ("Jaimie Lovell", "Director, Reputation, Government & Industry Affairs"),
        ("Peter Atkin", "Chief Legal Officer"),
        ("Caroline Israel", "Chief Transformation & Strategy Officer"),
    ]),
    ("Coles Group", "Retail",
     "https://www.colesgroup.com.au/about-us/?page=executive-leadership-team", [
        ("Leah Weckert", "Chief Executive Officer and Managing Director"),
        ("Charlie (Sharbel Raymond) Elias", "Chief Financial Officer"),
        ("Matt Swindells", "Chief Operations and Supply Chain Officer"),
        ("Anna Croft", "Chief Commercial and Sustainability Officer"),
        ("Michael Courtney", "Chief Customer Experience Officer"),
        ("David Brewster", "Chief Legal Officer"),
        ("Deb Yates", "Chief People Officer"),
        ("Claire Lauber", "Chief Executive, Liquor"),
        ("Clive Mathieson", "Group Corporate Affairs Officer"),
        ("Daniella Pereira", "Group Company Secretary"),
    ]),
    ("Rio Tinto", "Mining",
     "https://www.riotinto.com/en/about/executive-committee", [
        ("Simon Trott", "Chief Executive"),
        ("Bold Baatar", "Chief Commercial Officer"),
        ("Georgie Bezette", "Chief People Officer"),
        ("Trudi Charles", "Chief Legal, Governance & Corporate Affairs Officer"),
        ("Peter Cunningham", "Chief Financial Officer"),
        ("Mark Davies", "Chief Safety & Technical Officer"),
        ("Matthew Holcz", "Chief Executive, Iron Ore"),
        ("Katie Jackson", "Chief Executive, Copper"),
        ("Jérôme Pécresse", "Chief Executive, Aluminium & Lithium"),
    ]),
    ("Lynas Rare Earths", "Mining",
     "https://lynasrareearths.com/about-us/our-leadership-team/", [
        ("Pol Le Roux", "Interim Chief Executive Officer"),
        ("Gaudenz Sturzenegger", "Chief Financial Officer"),
        ("Dato Sri Mashal Ahmad", "Vice President Malaysia"),
        ("Mimi Afzan Afza", "Vice President People & Culture"),
        ("Daniel Havas", "Chief External Affairs Officer"),
        ("Jennifer Parker", "Vice President Corporate Affairs"),
        ("Sarah Leonard", "General Counsel and Company Secretary"),
        ("Chris Jenney", "Vice President Sales & Market Development"),
    ]),
    ("IGO Limited", "Mining",
     "https://www.igo.com.au/site/our-business/leadership", [
        ("Ivan Vella", "Managing Director and Chief Executive Officer"),
        ("Marie Bourgoin", "Chief Operating Officer"),
        ("Suzy Retallack", "Chief People and Sustainability Officer"),
        ("Brett Salt", "Chief Development Officer"),
        ("Ian Rowe", "Interim Chief Financial Officer"),
    ]),
    ("Fortescue", "Mining",
     "https://www.fortescue.com/en/about-fortescue/board-and-leadership-team", [
        ("Dr Andrew Forrest AO", "Executive Chairman and Founder"),
        ("Dino Otranto", "Chief Executive Officer, Fortescue Metals"),
        ("Agustin (Gus) Pichot", "Chief Executive Officer, Growth and Energy"),
        ("Apple Paget", "Group Chief Financial Officer"),
    ]),
    ("Cochlear", "Healthcare",
     "https://www.cochlear.com/us/en/corporate/investors/corporate-information/executive-team", [
        ("Dig Howitt", "CEO & President"),
        ("Dr Sarah Thom", "Chief Financial Officer"),
        ("Jan Janssen", "Chief Technology Officer"),
        ("Lisa Aubert", "President, North America"),
        ("Stu Sayers", "President, Asia Pacific & Latin America"),
        ("Anthony Bishop", "President, Europe, Middle East and Africa (EMEA)"),
        ("Dean Phizacklea", "Senior Vice President, Global Strategic Marketing"),
        ("Karen O'Driscoll", "Chief Information Officer"),
        ("Greg Bodkin", "Senior Vice President, Global Supply Chain"),
        ("Sabina Sopov", "Chief People Officer, Global People & Culture"),
        ("Brian Kaplan", "Senior Vice President, Global Clinical Strategy and Innovation"),
        ("Rob McGrory", "Group General Counsel & Company Secretary"),
    ]),
    ("Fisher & Paykel Healthcare", "Healthcare",
     "https://www.fphcare.com/us/corporate/about-us/executive-management/", [
        ("Lewis Gradon", "Managing Director & Chief Executive Officer"),
        ("Lyndal York", "Chief Financial Officer"),
        ("Andrew Somervell", "Vice President – Products & Technology"),
        ("Justin Callahan", "Vice President – Sales & Marketing"),
        ("Andy Niccol", "Chief Operating Officer"),
        ("Nicola Talbot", "Vice President – Human Resources"),
        ("Winston Fong", "Vice President – Surgical Technologies"),
        ("Brian Schultz", "Vice President – Quality, Safety & Regulatory Affairs"),
        ("Jonti Rhodes", "Vice President – Network Design, Facilities, Infrastructure & Sustainability"),
        ("Marcus Driller", "Vice President – Corporate"),
        ("Desh Edirisuriya", "General Manager – New Zealand Operations"),
        ("Nicholas Fourie", "Vice President – Information & Communication Technology"),
        ("Raelene Leonard", "General Counsel & Company Secretary"),
    ]),
    ("Ramsay Health Care", "Healthcare",
     "https://www.ramsayhealth.com/en/about/board-and-management/", [
        ("Natalie Davis", "Group CEO & Managing Director"),
        ("Colleen Harris", "Group Executive People"),
        ("Dr Brindan Suresh", "Group Executive Health & Strategy"),
        ("Anthony Neilson", "Group Executive Finance"),
        ("Dr John Doulis", "Group Executive Technology & Digital"),
        ("Henrietta Rowe", "Group Executive Legal & Company Secretariat"),
        ("Stuart Winters", "Chief Operating Officer Australia"),
        ("Andrew Coombs", "Chief Commercial Officer Australia"),
        ("Nick Costa", "Managing Director Ramsay UK"),
        ("Pascal Roché", "Managing Director Ramsay Santé"),
        ("Joe O'Connor", "Managing Director Elysium Healthcare"),
    ]),
    ("Xero", "Technology",
     "https://www.xero.com/us/about/team/", [
        ("Sukhinder Singh Cassidy", "Chief Executive Officer"),
        ("Claire Bramley", "Chief Financial Officer"),
        ("Damien Coleman", "Chief Legal Officer & Company Secretary"),
        ("Diya Jolly", "President, Product, Engineering and Customer Experience"),
        ("Maninder Sawhney", "Chief Business Officer"),
        ("Angad Soin", "Managing Director, Australia & New Zealand and Global Chief Strategy Officer"),
        ("Michael Strickman", "Chief Marketing Officer"),
        ("Matan Bar", "Xero US CEO"),
        ("Paul Evans", "Managing Director, International Markets"),
        ("Kate Hayward", "Managing Director, UK"),
        ("Jonathan Meltzer", "Managing Director, US"),
        ("Bridget Snelling", "Country Manager, New Zealand"),
        ("Colin Timmis", "Regional Director, EMEA"),
        ("Koren Wines", "Regional Director, Asia"),
    ]),
    ("WiseTech Global", "Technology",
     "https://www.wisetechglobal.com/investors/our-leadership-team/", [
        ("Zubin Appoo", "Chief Executive Officer"),
        ("Richard White", "Co-Founder, Executive Director and Chief Innovation Officer"),
        ("Vlad Bilanovsky", "Chief Execution Officer"),
        ("Angelina McMenamin", "Head of Performance Culture"),
        ("Marijana Okanovic", "Head of Corporate Communications"),
        ("Steve Murdoch", "Global Head of Marketing"),
        ("Matt Fielder", "Head of Information Systems"),
        ("Gene Gander", "General Manager Global Sales"),
        ("Mark Hall", "Chief Acquisition and Integration Officer"),
        ("Maree Isaacs", "Executive Director, Co-Founder and Head of License Management"),
        ("Katrina Johnson", "Chief Governance Officer & Group Corporate Secretary"),
        ("Ian Larsen", "Head of Operations"),
        ("Tudor Maxwell", "Head of WiseTech Academy"),
        ("Jeff Howard", "Chief Financial Officer"),
        ("Gareth Russell", "Chief People Officer"),
        ("Brett Shearer", "CTO and Chief Architect"),
        ("Damien Sullivan", "General Counsel"),
    ]),
    ("Computershare", "Technology",
     "https://www.computershare.com/corporate/about-us/global-management-team", [
        ("Stuart Irving", "Chief Executive Officer and President"),
        ("Mike Anson", "Chief Risk Officer"),
        ("Hussain Baig", "Global Chief Operating Officer"),
        ("Peter Belval", "Chief of Staff"),
        ("John Britten", "Chief Corporate Development Officer"),
        ("Francis Catterall", "CEO, Employee Share Plans"),
        ("Fiona Chalmers", "CEO, Issuer Services"),
        ("Paul Conn", "President, Global Capital Markets"),
        ("Dominic Horsley", "Group Counsel and Company Secretary"),
        ("Jonathan Kidd", "Global Chief Information Security Officer"),
        ("Frank Madonna", "CEO, Computershare Corporate Trust"),
        ("Mark McDougall", "Global Chief Information Officer and Global CEO of Communication Services"),
        ("Lucy Newcombe", "Chief People and ESG Officer"),
        ("Nick Oldfield", "Chief Financial Officer"),
        ("Nicola Williams", "Chief Audit Executive"),
    ]),
]

# People added by hand (scripts/added_people.csv, see add_people.py --save). They are
# appended after each company's public people, with source = "manually added", and are
# never given an answer in truth.csv, so they are never scored.
ADDED_FILE = Path(__file__).resolve().parent / "added_people.csv"
added = {}
if ADDED_FILE.exists():
    with open(ADDED_FILE, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            added.setdefault(row["company_id"].strip(), []).append(row)
valid_ids = {f"R{i:02d}" for i in range(1, len(COMPANIES) + 1)}
unknown = set(added) - valid_ids
assert not unknown, f"added_people.csv has unknown company_id(s): {sorted(unknown)}"

OUT.mkdir(parents=True, exist_ok=True)
emp_cols = ["company_id", "company_name", "industry", "size", "employee_id",
            "full_name", "title", "department", "location", "source"]
with open(OUT / "employees.csv", "w", newline="", encoding="utf-8") as fe, \
     open(OUT / "sources.csv", "w", newline="", encoding="utf-8") as fs:
    we, ws = csv.writer(fe), csv.writer(fs)
    we.writerow(emp_cols)
    ws.writerow(["company_id", "employee_id", "full_name", "source_url", "accessed"])
    for i, (name, industry, url, people) in enumerate(COMPANIES, 1):
        cid = f"R{i:02d}"
        extra = added.get(cid, [])
        names = [p[0] for p in people] + [a["full_name"].strip() for a in extra]
        assert len(names) == len(set(names)), f"duplicate person in {name}"
        size = len(names)                     # people listed, public + manually added
        for j, (person, title) in enumerate(people, 1):
            eid = f"{cid}-E{j:03d}"
            # department/location not published, left blank
            we.writerow([cid, name, industry, size, eid, person, title, "", "", "public"])
            ws.writerow([cid, eid, person, url, ACCESSED])
        for j, a in enumerate(extra, len(people) + 1):      # IDs continue after the public ones
            we.writerow([cid, name, industry, size, f"{cid}-E{j:03d}", a["full_name"].strip(),
                         a["title"].strip(), a["department"].strip(), "", "manually added"])
# People who left (scripts/leavers.csv): their position becomes a vacant card, and if
# they joined another company in this data they are added there. No-op when empty.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import leavers  # noqa: E402
import pandas as pd  # noqa: E402

emp = pd.read_csv(OUT / "employees.csv")
emp_after, _ = leavers.apply(emp)
if emp_after is not emp:                         # only rewrite when someone actually left
    emp_after.to_csv(OUT / "employees.csv", index=False)
n_added = sum(len(v) for v in added.values())
print(len(COMPANIES), "companies,", sum(len(c[3]) for c in COMPANIES), "public people,",
      n_added, "manually added")
