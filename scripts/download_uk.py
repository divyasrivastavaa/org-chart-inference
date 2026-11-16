"""Download the raw UK government organograms (senior staff) into data/raw/uk/.

The only step that needs internet. scripts/build_uk.py then builds data/uk/ offline from
these files, so the raw CSVs and manifest.csv are committed to the repo.

Source: data.gov.uk, "Organogram of Staff Roles & Salaries" datasets, the newest
senior-staff CSV of each organisation on 2026-11-16 (URLs pinned below, so a rebuild
always uses the same files).
Licence: Open Government Licence v3.0. Contains public sector information licensed under
the Open Government Licence v3.0.

Chosen: 12 organisations (7 departments, 5 agencies / public bodies) whose file
downloads and parses cleanly, has one top post, at least 20 posts, and every
"Reports to Senior Post" points to a post in the same file.
Run:  python scripts/download_uk.py
"""
import csv
import urllib.request
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "uk"
DOWNLOADED = "2026-11-16"
LICENCE = "Open Government Licence v3.0"
S3 = "https://s3-eu-west-1.amazonaws.com/datagovuk-production-ckan-organogram/"

# (company_id, organisation, file URL) - company IDs in alphabetical order
FILES = [
    ("U01", "Department for Digital, Culture, Media and Sport",
     S3 + "organogram-department-for-culture-media-and-sport/resources/2026-10-30T15-59-13Z-2026-09-30-organogram-senior.csv"),
    ("U02", "Department for Energy Security and Net Zero",
     S3 + "desnz-organogram-of-staff-roles-salaries/resources/2026-11-03T17-01-52Z-2026-11-03-organogram-senior.csv"),
    ("U03", "Department for Environment, Food and Rural Affairs",
     S3 + "organogram-department-for-environment-food-and-rural-affairs/resources/2026-10-30T09-00-02Z-2026-10-30-organogram-senior.csv"),
    ("U04", "Department for Work and Pensions",
     S3 + "organogram-of-staff-roles-salaries30092019/resources/2026-11-05T09-52-31Z-2026-11-06-organogram-senior.csv"),
    ("U05", "Environment Agency",
     S3 + "organogram-environment-agency/resources/2026-10-30T09-04-06Z-2026-10-30-organogram-senior.csv"),
    ("U06", "Foreign, Commonwealth and Development Office",
     S3 + "fcdo-organograms/resources/2026-10-29T11-26-37Z-2026-10-29-organogram-senior.csv"),
    ("U07", "HM Revenue and Customs",
     S3 + "organogram-hm-revenue-and-customs/resources/2026-10-30T16-14-38Z-2026-09-30-organogram-senior.csv"),
    ("U08", "Home Office",
     S3 + "organogram-home-office/resources/2026-10-05T13-47-05Z-2025-12-31-organogram-senior.csv"),
    ("U09", "Homes England",
     S3 + "organogram-of-staff-roles-salaries-september-2026/resources/2026-10-09T10-58-11Z-2026-09-30-organogram-senior.csv"),
    ("U10", "NHS Blood and Transplant",
     S3 + "organogram-nhs-blood-and-transplant/resources/2026-10-29T14-55-44Z-2026-10-29-organogram-senior.csv"),
    ("U11", "United Kingdom Atomic Energy Authority",
     S3 + "organogram-uk-atomic-energy-authority/resources/2026-11-02T08-24-18Z-2026-09-30-organogram-senior.csv"),
    ("U12", "Valuation Office Agency",
     S3 + "organogram-valuation-office-agency/resources/2026-10-30T08-06-08Z-2026-10-30-organogram-senior.csv"),
]


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    rows = []
    for cid, org, url in FILES:
        req = urllib.request.Request(url, headers={"User-Agent": "org-chart-inference/1.0"})
        data = urllib.request.urlopen(req, timeout=120).read()
        name = f"{cid}.csv"
        (RAW / name).write_bytes(data)          # exactly as published, not re-encoded
        rows.append([cid, org, name, url, LICENCE, DOWNLOADED])
        print(f"{cid} {org}: {len(data):,} bytes")
    with open(RAW / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["company_id", "organisation", "file", "url", "licence", "downloaded"])
        w.writerows(rows)


if __name__ == "__main__":
    main()
