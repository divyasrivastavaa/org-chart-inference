# Phase 2: database — plan

This phase moves the project from CSV files to a Postgres database (a free Supabase project
in the Mumbai region). It adds:

1. One database that holds everything: companies, people, jobs, reporting lines, runs and
   the answer key.
2. A way to add new companies and new people.
3. A full job history: moves between companies and title changes inside a company. Nothing
   is overwritten.
4. Refreshes that re-infer only the companies where something changed.
5. The cost of every run, plus logging with Logfire.
6. A screen where anything in the database can be queried (read-only SQL).

New real data comes from **People Data Labs (PDL)**. The free company dataset has companies
but no people or titles. The free person API gives only about **100 credits a month**.

We build one step at a time. Each step ends with a check, and the next step starts only
when that check passes.

---

## Ground rules (every step)

**PDL credits are limited:**
- Build everything on a small amount of data first. Add more data only at the end.
- **Person API:** make one small test call first (1–2 people) and save the raw JSON to a
  file. Then show which fields came back (name, job title, company, start/end dates,
  seniority, department), and **wait for an OK** before any bigger run.
- **Cache every API response to a file** (`data/pdl_cache/`, git-ignored). Before any call,
  the code looks in the cache first, so a rerun never spends credits again.
- Every PDL script has a hard credit limit (`--max-credits`, default 2). It counts the
  credits it has used in `data/pdl_cache/credits_used.json` and stops before going over.

**Secrets:**
- No key or password is ever written in code. Everything is read from `st.secrets`
  (`.streamlit/secrets.toml` locally, which is git-ignored, or Streamlit Cloud → Settings →
  Secrets when deployed).
- Secret names used in this phase:

| Secret | What it is for |
|---|---|
| `DATABASE_URL` | Supabase session pooler address, with `[YOUR-PASSWORD]` as a placeholder |
| `DATABASE_PASSWORD` | the database password; `db/connect.py` puts it into `DATABASE_URL`, percent-encoded |
| `DATABASE_READONLY_URL` | the read-only SQL tab (Step 10) |
| `PDL_API_KEY` | People Data Labs |
| `LOGFIRE_TOKEN` | Logfire logging |
| `APP_EDIT_PASSWORD` | unlocks adding or changing data in the hosted app |
| `GEMINI_API_KEY` | already used by the optional Gemini judge |

**Keep the app light:**
- New packages: only `psycopg[binary]` (Postgres driver) and `logfire`. No torch.
- Streamlit Cloud's free tier has about 1 GB of RAM.

**Measure every change:**
- The inference code itself does not change in this phase. Results read from the database
  must match the CSV results on all four datasets (Benchmark seed 42, held-out seed 7,
  ASX/NZX, UK), compared with the baseline in `docs/experiments.md`.

---

## Step 1 — Schema (this step)

- `db/schema.sql` creates the tables: `companies`, `people`, `employments`,
  `reporting_lines`, `runs`, `answer_key`.
- `db/README.md` explains each table in simple words and gives 5 example queries.

Key decisions:

- **A person is separate from a job.** `people` has one row per person. `employments` has
  one row per job (person + company + title + dates). Someone who changes company keeps the
  same `person_id`.
- **Never overwrite.**
  - **Title change:** close the old employment row (`end_date`, `is_current = false`) and
    add a new row with `change_type = 'title_change'`.
  - **Move to another company:** close the old row and add a row at the new company with
    `change_type = 'job_change'`.
  - `previous_employment_id` links each new row to the one it replaced.
- **Reporting lines keep their history too.** A refresh writes a new row only for a person
  whose line changed, and marks the old row `is_current = false`. Each row belongs to a
  `run`.
- **`needs_refresh` is set automatically.** A database trigger marks the company whenever an
  employment row is added or changed. It can't be forgotten, whether the change comes from
  the app, a script or PDL.
- **IDs stay the same.** Existing IDs are kept (`C001`, `R01`, `U01`, `C001-E0001`), so the
  app and answer key still line up. New PDL rows get a `P-` prefix, and companies created in
  the app get `N01`, `N02` and so on.
- **The answer key is its own table.** The inference code never reads it.
- **Row level security is on for every table.** This blocks Supabase's public web API. The
  app connects with its own login instead.

Check: the SQL file runs on an empty Postgres database without errors. This happens in
Step 2, since nothing is connected yet.

## Step 2 — Connect and load the existing data (small first)

1. Run `db/schema.sql` once in Supabase (SQL Editor → paste → Run).
2. **Connect.** We connect with two secrets:
   - `DATABASE_URL`: Supabase's **Session pooler** address, because Streamlit Cloud can't
     reach the direct address (it is IPv6 only). It has `[YOUR-PASSWORD]` as a
     placeholder.
   - `DATABASE_PASSWORD`: the password itself.

   `db/connect.py` (`get_connection()`) puts the password into the address,
   percent-encoded, and connects with psycopg. `scripts/check_db.py` prints only the
   Postgres version, the tables and their row counts. **Done:** PostgreSQL 17, 6 empty
   tables.
3. **Load a small sample.** `scripts/load_db.py` reads the CSVs the current pipeline builds
   and fills the tables:

| From | To |
|---|---|
| `employees.csv` company columns | `companies` (`source` = benchmark / asx_nzx / uk_organogram), `needs_refresh = true` |
| `employees.csv` people | `people` + one `employments` row each (`change_type = 'initial'`, `is_current = true`) |
| `truth.csv` | `answer_key` |

   - **Duplicate rows** (`-DUP`) are skipped.
   - **Benchmark people "missing from the data"** are not loaded as people. They stay only
     as `true_manager_id` in the answer key, so the inference can't see them.
   - **ASX/NZX answer key:** only the 43 managers confirmed in writing are loaded.
   - **UK and Benchmark answer key:** a blank manager means top of the organisation.
   - **Safe to rerun:** every insert skips rows that already exist.
   - **Sample:** all 20 ASX/NZX companies, 2 UK organisations (U01, U02) and 10 Benchmark
     companies (C001–C010, seed 42). **Done:** 32 companies, 2,144 people, 2,144
     employments, 1,969 answer-key rows.
4. **Later, at the end (Step 11):** `--benchmark 500 --uk 12` loads everything (about 158k
   Benchmark people). That fits in the free 500 MB.
5. The held-out seed 7 dataset stays as files. It is only used for measuring.

Check: row counts in the database = row counts in the CSVs (without duplicates).

## Steps 3 and 4 — Inference from the database, refresh only changed companies

One command does both: `python refresh.py` (later also a button in the app).

1. **Open a run.** It writes a row in `runs`: trigger, start time, status `running`.
2. **Pick companies.** It takes the companies with `needs_refresh = true` **that have
   current employees**. Companies that only appear in someone's past job history are never
   refreshed.
3. **Infer.** It loads their current employments from the database and runs
   `infer.predict()` **unchanged**: same rules, same decision tree, same 0.7 threshold.
   - Each company is re-inferred **as a whole**, because a new person can change other
     people's lines. For example, a newly added VP becomes the manager of the Directors in
     that function.
   - Other companies are not touched.
   - **PDL department words are translated first** (`human_resources` → `Human Resources`,
     words with no match → blank). Otherwise `infer.py` would read them as UK unit names.
     The rules are unchanged; only the input is mapped.
4. **Save, one transaction per company,** so a crash never leaves a half-updated chart:
   - all the company's current `reporting_lines` get `is_current = false`;
   - **all** its new lines are inserted with `is_current = true` and this `run_id`;
   - the company gets `needs_refresh = false`, `last_refreshed_at` and `last_run_id`.
   Writing all lines (not only the changed ones) keeps every run's full chart in the
   history. The cost is small, because only flagged companies are refreshed.
5. **Close the run:** end time, status (`succeeded`, or `failed` with the error), companies
   and people processed, lines written, threshold and cost. Cost is $0 for the rules: no
   Gemini tokens, no PDL credits.
6. **Nothing to do:** if no company needs a refresh, the run is still recorded, with 0
   companies, and nothing else happens.

So "refresh only for new people, people who changed jobs and people newly added" means:
only their companies are re-inferred.

**Check, done (E8 in `docs/experiments.md`):**
- **Run 1:** refreshed 33 companies (the 32 loaded + Atlassian), 2,194 people, 2,194 lines,
  9 s, $0. The 233 past-only companies were not touched.
- **Run 2:** 0 companies, nothing done.
- **Same numbers as the CSVs.** `scripts/compare_db_csv.py` compares the database with the
  CSVs for the same companies:
  - ASX/NZX and UK: identical.
  - Benchmark: 5 more lines, all right. The CSV run sometimes kept a duplicate (`-DUP`) row
    instead of the original; the database has only originals.
- **Atlassian (no answer key):** 0 of 50 lines drawn. Only senior people were fetched, so
  most of their managers are not in the data, and 10 titles couldn't be parsed.

## Step 5 — PDL companies (small first)

- Download the PDL free company dataset once, by hand, into `data/raw/pdl/` (git-ignored).
- Write `db/load_pdl_companies.py`. It loads a **small sample first**: about 20 companies
  with `source = 'pdl'` and `source_ref` = the PDL company id. That covers a few
  industries and sizes, and favours companies with a known website or LinkedIn page.
- These companies start with no people. People arrive in Step 6.

**Be honest about scale:** 100 credits a month is about 100 people in total, so PDL can't
fill companies of 100+ employees. PDL is a small **real** sample for testing job changes.
The Benchmark stays the dataset for scale and accuracy numbers.

Check: 20 PDL companies in `companies`, no credits spent (this dataset is a file
download).

## Step 6 — PDL person API: one test call, then stop

1. `scripts/pdl_test.py` makes **one Person Search call** with `size = 2`, for Atlassian.
   - Before calling, it checks the cache (`data/pdl/cache/<hash of the request>.json`). If
     the response is already there, it is used and no call is made.
   - The key comes from `PDL_API_KEY` in the secrets.
   - Only the fields this project uses are saved (no emails, phone numbers, social links or
     addresses).
   - **Done:** 2 credits. Everything we need came back; seniority is sometimes missing.
2. Save the JSON (only the needed fields). Print a small table of which fields came back, and which are empty
   or hidden on the free plan:
   - name: `full_name`
   - job title: `job_title`
   - company: `job_company_name`, `job_company_id`
   - start/end dates: `job_start_date`, and per job in `experience[]`
   - seniority: `job_title_levels`
   - department: `job_title_role`, `job_title_sub_role`
   - the job history: `experience[]` (company, title, start date, end date)
3. **Stop and wait for an OK.** Nothing else is called until then.
4. After the OK: map the fields to tables.
   - **Person:** a `people` row, `person_id = 'P-' + PDL id`.
   - **Jobs:** each `experience[]` entry becomes an `employments` row. Past jobs are closed;
     the current job is `is_current = true`.
   - **Unknown employers:** a past employer that isn't in `companies` is added with only its
     name and PDL id.
   - **Year-month dates** become the 1st of that month.
   - **Done** with `scripts/pdl_load.py`: one call, 50 senior Atlassian people (cxo / vp /
     director), 50 credits. Loaded 50 people, Atlassian + 233 past employers
     (`pdl_history`) and 486 jobs: 50 initial, 272 job changes, 164 title changes.
     The credit header then said 0 left, so **no PDL call is made without an explicit OK**.

Privacy: these are real people.
- The cached JSON stays git-ignored in `data/pdl/cache/`, stripped to the needed fields
  (`scripts/pdl_strip_cache.py` stripped the first two files).
- The hosted app shows only name, title, company and dates. Nothing else from the profile
  (no contact details, no social links).
- Logs record IDs and counts, never names.

Check: the field table is shown, and at most 2 credits were used.

## Step 7 — Track job changes

- **Re-fetch** a known person, only on request and always through the cache. Compare their
  latest job with their current `employments` row:
  - **Same company, different title:** close the old row and add a `title_change` row.
  - **Different company:** close the old row and add a `job_change` row at the new
    company (created if missing). Both companies get `needs_refresh = true`.
  - **Nothing changed:** nothing is written.
- The same logic is used by the app (Step 8), so every change goes through one function:
  `record_job_change(person_id, company_id, title, start_date)`.

Check: example query 1 shows the full history of a test person after one title change and
one move.

## Step 8 — Add and change data in the app

A new **Database** page in the app has these forms:

- **Add a company:** name, industry, size, country (`source = 'app'`).
- **Add a person:** name, title, department, company, start date (`change_type =
  'new_person'`).
- **Change title:** same company (`title_change`).
- **Moves to another company:** `job_change`, with the old job closed.
- **Leaves:** closes the current job.
- **Refresh changed companies:** runs `refresh.py` (Steps 3 and 4) and shows the run's
  numbers.

Rules:

- **Writes are locked on the hosted app.** Adding or changing data needs
  `APP_EDIT_PASSWORD`, because the database is shared by every visitor. Without the
  password, the page is view-only.
- **What-if stays separate.** The existing What-if buttons (add, remove, promote) stay as
  they are: an in-memory sandbox, per visitor, never saved. The Database page is the place
  where real changes are recorded.
- **Where the app reads from.** The company list and org chart read from the database when
  `DATABASE_URL` is set, and fall back to the CSVs when it isn't. Local runs and tests
  still work without a database.

Check: add a person in the app, refresh, and see them on the chart. Query 1 shows their
history. Only their company was refreshed.

## Step 9 — Logfire and cost per run

**Logfire:**
- `logfire.configure(token=st.secrets["LOGFIRE_TOKEN"], service_name="org-chart")`.
- One span per run, one span per company inside it, and small spans for read, infer and
  write.
- **Logged:** counts, durations, credits, tokens, errors.
- **Not logged:** names, titles or keys.
- With no token, logging is simply switched off.

**Cost per run** is stored in `runs`:
- **Gemini:** calls, input tokens and output tokens, read from each response's usage
  information. `judge_gemini.py` doesn't record tokens yet; this is one small change.
- **PDL:** credits used, from the credit counter.
- **`cost_usd`:** tokens × the model's list price per million tokens, plus credits × price
  per credit.
  - The prices are constants in one place, each with the date it was checked.
  - On free tiers the real bill is $0, but the list-price cost is still recorded, so it
    shows what the same run would cost on a paid plan.
- **Supabase and Streamlit Cloud:** free tiers, $0. Noted in the write-up.
- **Time:** `finished_at - started_at`, per run and per company in Logfire.

Check: one refresh run shows up in Logfire, and its `runs` row has counts, tokens and
cost. Example query 4 lists it.

## Step 10 — Read-only SQL tab

A **SQL** tab in the app: type a query, see the result as a table.

Read-only by design, with several layers:
1. **A database role that can only read.** `app_readonly` has `SELECT` on these tables and
   a row level security policy that lets it read them. It is created once in Supabase, with
   its password set there (never in code), and its connection string goes in
   `DATABASE_READONLY_URL`.
2. **Read-only transactions.** The role has `default_transaction_read_only = on`, and the
   app also runs every query inside a read-only transaction.
3. **Limits.** `statement_timeout = 5s`, and at most 1,000 rows are shown.
4. **Examples.** The 5 queries in `db/README.md` are offered as buttons.

Check: `select` works. `insert`, `update`, `delete` and `drop` are refused by the database
itself, not just by the app.

## Step 11 — Scale up (last)

- Load all Benchmark companies, if Step 2 used only the sample.
- **More PDL people only after an explicit OK**, within the monthly credits, always cached.
- Re-measure:
  - accuracy on all four datasets;
  - refresh time for one changed company vs a full run;
  - cost per run.
- Log the results in `docs/experiments.md` and update the README.

---

## Order and status

| Step | What | Status |
|---|---|---|
| 1 | Schema + plan + db/README | done |
| 2 | Connect + load existing data (small, then all) | connected; small sample loaded (full load in Step 11) |
| 3 | Inference from the database, same numbers | done (`refresh.py`, E8) |
| 4 | Refresh only changed companies | done (`refresh.py`, E8) |
| 5 | PDL companies (small sample) | not started (Atlassian + past employers came from Step 6) |
| 6 | PDL person API: one test call, wait for OK | done: test (2 credits) + one run of 50 people (50 credits) |
| 7 | Job-change tracking | |
| 8 | Add / change data in the app | |
| 9 | Logfire + cost per run | |
| 10 | Read-only SQL tab | |
| 11 | Scale up | |

## Open decisions (before the step that needs them)

- **Step 5:** which ~20 PDL companies (country, industries, sizes).
- **Step 6:** after the test call, whether the fields are good enough, and how many
  credits to spend.
- **Step 8:** whether the hosted app should allow edits at all (with a password), or only
  local runs.
