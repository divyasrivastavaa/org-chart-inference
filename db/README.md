# Database (Phase 2)

The tables live in a Postgres database (Supabase). The full schema is in
[schema.sql](schema.sql), and the step-by-step plan is in [../docs/db_plan.md](../docs/db_plan.md).

Two rules shape every table:

- **History is never overwritten.** A job change or title change adds a new row. The old row
  is closed (it gets an end date and `is_current = false`) but never deleted.
- **Only changed companies are re-inferred.** Any new or changed job sets the company's
  `needs_refresh` flag, and the next refresh run handles only those companies.

## Tables

| Table | One row is … | In simple words |
|---|---|---|
| `companies` | a company or organisation | Name, industry, size, where it came from (`source`: benchmark, asx_nzx, uk_organogram, pdl, pdl_history, manual) and `needs_refresh`, which is true when its org chart is out of date. |
| `people` | a person | Their name and where they came from. A person appears once, even after moving between companies. Existing data keeps its old IDs (`C001-E0001`). |
| `employments` | one job: a person, at a company, with a title, for a period | The job history. `is_current` marks today's job. `change_type` says why the row exists: `initial` (first load, or the earliest job in a PDL history), `new` (person added by hand), `job_change` (came from another company) or `title_change` (same company, new title). `seniority` is the source's level when it gives one (PDL: e.g. `vp`). `previous_employment_id` points to the row it replaced, so a career can be followed step by step. Leaving is shown by the old row being closed. |
| `reporting_lines` | a person's inferred manager in one run | `manager_id` is the drawn line (confidence at or above the threshold). It is empty when the model abstained, and `reason` says why. `proposed_manager_id` is the best candidate even when not drawn. `judge` is rules, gemini or interim. A new row is written only when a line changes; the old row gets `is_current = false`. |
| `runs` | one inference run | When it started and finished, its status, how many companies and people it processed, how many lines changed, the Gemini calls and tokens, the PDL credits used and the cost in USD. |
| `answer_key` | a person's true manager | Used only to measure precision and coverage. The inference code never reads it. |

How they connect: `people` → `employments` ← `companies`, and each `reporting_lines` row
belongs to one `run`, one company, one person and (if drawn) one manager, who is also in
`people`.

**Safety:** row level security is on for every table, which blocks Supabase's public web
API. The app connects directly with its own login, and the read-only SQL tab uses a role
that can only `SELECT`.

## Example queries

**1. One person's job history** (oldest first)

```sql
select p.full_name, c.name as company, e.title, e.start_date, e.end_date,
       e.is_current, e.change_type, e.recorded_at
from employments e
join people    p on p.person_id  = e.person_id
join companies c on c.company_id = e.company_id
where p.person_id = 'C001-E0001'          -- or: where p.full_name ilike '%some name%'
order by e.start_date nulls first, e.recorded_at;
```

**2. A company's current org chart** (each person, their title and their manager)

```sql
select p.full_name, e.title, m.full_name as manager, rl.confidence, rl.reason, rl.judge
from reporting_lines rl
join people p      on p.person_id = rl.person_id
left join people m on m.person_id = rl.manager_id
left join employments e on e.person_id = rl.person_id
                       and e.company_id = rl.company_id and e.is_current
where rl.company_id = 'C001' and rl.is_current
order by m.full_name nulls first, p.full_name;
```

**3. Companies that need a refresh**

```sql
select c.company_id, c.name, c.source, c.last_refreshed_at,
       count(e.employment_id) as current_people
from companies c
left join employments e on e.company_id = c.company_id and e.is_current
where c.needs_refresh
group by c.company_id
order by c.name;
```

**4. Cost per run** (newest first)

```sql
select run_id, trigger, status, started_at,
       finished_at - started_at                    as duration,
       companies_processed, people_processed, lines_written,
       gemini_input_tokens + gemini_output_tokens  as gemini_tokens,
       pdl_credits_used, cost_usd,
       round(cost_usd / nullif(companies_processed, 0), 4) as cost_per_company
from runs
order by run_id desc
limit 20;
```

**Total cost and credits per day and per trigger**

```sql
select started_at::date                           as day,
       trigger,
       count(*)                                   as runs,
       sum(companies_processed)                   as companies,
       sum(people_processed)                      as people,
       sum(gemini_input_tokens)                   as gemini_input_tokens,
       sum(gemini_output_tokens)                  as gemini_output_tokens,
       sum(pdl_credits_used)                      as pdl_credits,
       sum(cost_usd)                              as cost_usd
from runs
group by 1, 2
order by 1 desc, 2;
```

Prices are set in `db/costs.py` (Gemini: list price per token, with its source link;
PDL: $0 per credit on the free plan). PDL calls are recorded as runs with
`trigger = 'pdl_import'` by `scripts/pdl_record_runs.py`.

**5. People without a manager** (in one company, with the reason)

```sql
select p.full_name, e.title, rl.reason, rl.proposed_manager_id, rl.confidence
from reporting_lines rl
join people p on p.person_id = rl.person_id
left join employments e on e.person_id = rl.person_id
                       and e.company_id = rl.company_id and e.is_current
where rl.company_id = 'C001'
  and rl.is_current
  and rl.manager_id is null
  and rl.reason is distinct from 'top of company'
order by rl.reason, p.full_name;
```
