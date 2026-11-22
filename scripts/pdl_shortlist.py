"""
scripts/pdl_shortlist.py  -  picks real companies from PDL's free company dataset.
No API calls: reads data/pdl/free_company_dataset.csv.zip (really a gzip file) in chunks.

  python scripts/pdl_shortlist.py pool
      -> data/pdl/company_pool.csv: every usable company (Australia, India, UK, US; 51-5000
         people; software, financial services, healthcare, retail / e-commerce, IT consulting;
         with a domain, founding year and city; no public bodies, Atlassian, or duplicates)
  python scripts/pdl_shortlist.py extend --people 10000
      -> adds about that many planned people to data/pdl/company_shortlist.csv, keeping the
         same country / size band / industry mix.
All files are in data/ (git-ignored).
"""
import argparse
import math
import random
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ZIP = ROOT / "data" / "pdl" / "free_company_dataset.csv.zip"
POOL = ROOT / "data" / "pdl" / "company_pool.csv"
SHORTLIST = ROOT / "data" / "pdl" / "company_shortlist.csv"
SEED = 42
COLS = ["id", "name", "website", "industry", "size", "founded", "locality", "region", "country"]
COUNTRIES = ["australia", "india", "united kingdom", "united states"]
GROUPS = {"computer software": "Software", "internet": "Software",
          "financial services": "Financial services", "banking": "Financial services",
          "hospital & health care": "Healthcare", "medical practice": "Healthcare",
          "medical devices": "Healthcare", "pharmaceuticals": "Healthcare",
          "retail": "Retail / e-commerce",
          "information technology and services": "IT consulting"}
ECOM = r"\b(?:shop|store|commerce|ecommerce|e-commerce|cart|marketplace)\b"
# People planned per company (the top band is capped at 2,000; at least 100 everywhere)
BANDS = {"51-200": (100, 200), "201-500": (201, 500), "501-1000": (501, 1000),
         "1001-5000": (1001, 2000)}
BAND_WEIGHT = {"51-200": .20, "201-500": .25, "501-1000": .20, "1001-5000": .25}
SHARE = {b: w / sum(BAND_WEIGHT.values()) for b, w in BAND_WEIGHT.items()}
BAD_DOMAIN = (r"(?:^|\.)(?:linkedin|facebook|instagram|twitter|x|youtube|wixsite|wordpress|"
              r"blogspot|google|yahoo|gmail|medium|github)\.")
EXCLUDE = ["atlassian", "commonwealth bank", "westpac", "national australia bank", "anz group",
           "anz bank", "macquarie group", "macquarie bank", "telstra", "tpg telecom",
           "wesfarmers", "woolworths", "coles group", "coles supermarkets", "rio tinto", "lynas",
           "igo limited", "fortescue", "cochlear", "fisher & paykel", "ramsay health", "xero",
           "wisetech", "computershare", "department for digital", "department for energy",
           "department for environment", "department for work", "environment agency",
           "foreign, commonwealth", "hm revenue", "home office", "homes england", "nhs blood",
           "atomic energy authority", "valuation office"]
PUBLIC_BODIES = (r"|\bnhs\b|\breserve bank\b|\bcentral bank\b|\bfederal reserve\b|\bministry\b"
                 r"|\bgovernment\b|\bgovt\b|\bdepartment of\b|\bcouncil\b"
                 r"|\bnational informatics\b|\bveterans affairs\b|\bstate of\b|\bcity of\b"
                 r"|\bcounty\b|\bpublic health\b|\buniversity\b")
EX = "|".join(re.escape(x) for x in EXCLUDE) + r"|^asx(?: limited| ltd)?$" + PUBLIC_BODIES


def norm(name):
    s = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    s = re.sub(r"\b(ltd|limited|llc|inc|plc|pty|pvt|private|corp|corporation|co|company|group)\b",
               " ", s)
    return " ".join(s.split())


def build_pool():
    parts, rows = [], 0
    for ch in pd.read_csv(ZIP, compression="gzip", usecols=COLS, dtype=str, keep_default_na=False,
                          chunksize=500_000, on_bad_lines="skip", encoding="utf-8",
                          encoding_errors="replace"):
        rows += len(ch)
        ch["country"] = ch.country.str.strip().str.lower()
        ch["size"] = ch["size"].str.strip()
        ch["industry"] = ch.industry.str.strip().str.lower()
        ch = ch[ch.country.isin(COUNTRIES) & ch["size"].isin(BANDS) & ch.industry.isin(GROUPS)].copy()
        ch["domain"] = ch.website.str.strip().str.lower().str.replace(r"^https?://", "", regex=True) \
            .str.replace(r"^www\.", "", regex=True).str.rstrip("/")
        ok = ch.domain.str.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+") \
            & ~ch.domain.str.contains(BAD_DOMAIN, regex=True)
        ch.loc[~ok, "domain"] = ""
        year = pd.to_numeric(ch.founded, errors="coerce")
        ch["year"] = year.where(year.between(1800, 2026))
        ch = ch[ch.name.str.contains(r"[a-z]", regex=True) & ch.name.str.len().between(2, 80)
                & ~ch.name.str.contains(EX, regex=True)
                & ~ch.domain.str.contains("atlassian", regex=False)]
        parts.append(ch[["id", "name", "domain", "industry", "size", "year", "locality", "region",
                         "country"]])
    df = pd.concat(parts, ignore_index=True)
    df["group"] = df.industry.map(GROUPS)
    shop = df.industry.eq("internet") & (df.name.str.contains(ECOM, regex=True)
                                         | df.domain.str.contains(ECOM, regex=True))
    df.loc[shop, "group"] = "Retail / e-commerce"
    df = df[df.domain.ne("") & df.year.notna() & df.locality.ne("")].copy()
    df["nn"] = df.name.map(norm)
    df["r"] = np.random.default_rng(SEED).random(len(df))
    df = df.sort_values("r")
    df = df[df.nn.ne("")].drop_duplicates(["country", "nn"]).drop_duplicates("domain")
    df["year"] = df.year.astype(int)
    df.drop(columns="nn").to_csv(POOL, index=False, encoding="utf-8")
    print(f"read {rows:,} rows; {len(df):,} usable companies -> {POOL.relative_to(ROOT)}")


def planned(domain, band):
    lo, hi = BANDS[band]
    r = random.Random(f"size-{domain}")
    return int(round(math.exp(r.uniform(math.log(lo), math.log(hi)))))


def extend(people):
    pool = pd.read_csv(POOL, keep_default_na=False)
    short = pd.read_csv(SHORTLIST, keep_default_na=False) if SHORTLIST.exists() else \
        pd.DataFrame(columns=["name", "domain", "industry", "size", "year_founded", "city",
                              "country", "planned_people"]).astype({"planned_people": int})
    group = pool.set_index("domain").group
    if not short.domain.isin(group.index).all():
        sys.exit("Stopped: a shortlisted company is not in the pool.")
    short["group"] = short.domain.map(group)
    now = short.groupby(["country", "size", "group"]).planned_people.sum().to_dict()
    groups = sorted(pool.group.unique())
    final = int(short.planned_people.sum()) + people
    target = {(c, b, g): final / 4 * SHARE[b] / len(groups)
              for c in COUNTRIES for b in BANDS for g in groups}
    free = pool[~pool.domain.isin(short.domain)]
    queues = {k: list(g.itertuples()) for k, g in free.groupby(["country", "size", "group"])}
    added, left = [], people
    while True:
        best = None
        for key, goal in target.items():
            q = queues.get(key)
            if not q:
                continue
            n = planned(q[0].domain, key[1])
            if n > left:
                continue
            gap = (goal - now.get(key, 0)) / goal
            if best is None or gap > best[0]:
                best = (gap, key, n)
        if best is None:
            break
        _, key, n = best
        row = queues[key].pop(0)
        added.append({"name": row.name, "domain": row.domain, "industry": row.industry,
                      "size": row.size, "year_founded": row.year, "city": row.locality,
                      "country": row.country, "planned_people": n})
        now[key] = now.get(key, 0) + n
        left -= n
    if added:
        out = pd.concat([short.drop(columns="group"), pd.DataFrame(added)], ignore_index=True)
        out.to_csv(SHORTLIST, index=False, encoding="utf-8")
    print(f"added {len(added)} companies, {people - left:,} planned people; "
          f"shortlist now {len(short) + len(added)} companies")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["pool", "extend"])
    ap.add_argument("--people", type=int, default=0)
    args = ap.parse_args()
    build_pool() if args.command == "pool" else extend(args.people)
