# Running this pipeline locally

## 1. Folder layout

```
your-project/
├── run_all_in_one.py                   <- for a CSV-based "today" (see Option A below)
├── build_full_recompute_real_day.py    <- for a REAL day export (see Option B below)
├── hhs.py                              <- your scheduling schema
├── data/
│   ├── users-new.json
│   ├── clients-new.json
│   ├── VisitExport.csv
│   └── driving_data.json
├── data_today/
│   ├── today_patients.csv              <- for run_all_in_one.py
│   ├── today_carers.csv                <- for run_all_in_one.py
│   ├── patient_YYYY-MM-DD.json         <- for build_full_recompute_real_day.py
│   └── caregivers_YYYY-MM-DD.json      <- for build_full_recompute_real_day.py
├── 1_build_roster.py, 2_build_output.py, ...   <- legacy standalone scripts (see Option C)
└── run_pipeline.py                     <- legacy runner for Option C
```

Everything generated lands in `./output/`, created automatically.

## 2. One-time setup

```
pip install openpyxl pydantic
```

## 3. Which script to run

### Option A: `run_all_in_one.py` -- you supply today's patients/carers as CSV

Use this when you don't have a "today" export from your own system yet -- you fill in two
simple CSVs by hand (or however you generate them):

- `data_today/today_patients.csv` -- `client_name,start_time,end_time` (a double-up is just
  two rows for the same client with overlapping times, detected automatically)
- `data_today/today_carers.csv` -- `carer_name,shift_start,shift_end`

Edit `TARGET_DATE` near the top of the script to the date those CSVs are for, then:

```
python3 run_all_in_one.py
```

### Option B: `build_full_recompute_real_day.py` -- you have a real day export

Use this when your own system has already produced a `patients.json` / `caregivers.json`
for a specific day (in `hhs` schema format). This script trusts **only** identity (pid/cid,
gender, location) and the raw need/availability time from those files -- everything else
(duration, soft/hard windows, priorities, violation levels, extend_feasibility,
caregiver_usage_priority) is discarded and recomputed from your full history, the same way
Option A builds these fields from CSV input.

Edit these two lines near the top of the script to match your actual filenames:
```python
DAY_PATIENTS_FILENAME = 'patient_2026-08-27.json'
DAY_CAREGIVERS_FILENAME = 'caregivers_2026-08-27.json'
```
`TARGET_DATE` is read automatically from the `"date"` field inside your patients file --
no need to edit it separately. Then:

```
python3 build_full_recompute_real_day.py
```

### Option C: the legacy separate numbered scripts

Older, per-step versions of this pipeline (`1_build_roster.py` through
`17_build_day_analysis_week.py`, run via `run_pipeline.py`). **These are stale** -- they
don't have the extend_feasibility classifier, the actual-time window derivation, the 0.01
weight floor, or the DISLIKES mechanism that Options A and B have. Only use these if you
specifically want one of the standalone Excel workbook reports they produce (roster
coverage, geographic coverage, cancellation patterns, etc.) -- not for generating the
phase-2 patient/caregivers/feasibility files.

```
python3 run_pipeline.py --all-reports
```

## 4. DISLIKES

Both `run_all_in_one.py` and `build_full_recompute_real_day.py` declare a `DISLIKES` list
near the top -- `(client, carer)` pairs that must never be paired. Their shared history is
stripped from the roster entirely (so it doesn't distort anyone else's statistics either),
and a final weight=0.0 override is applied as a safety net. Add more pairs to this list as
needed -- it's the only place a feasibility weight of exactly 0.0 is allowed to originate;
everywhere else, real history gets at least a 0.01 floor so it's never confused with an
intentional dislike.

## 5. Verified

Both Option A and Option B have been tested end-to-end against this exact folder layout --
including a real 528-patient / 62-caregiver day export -- with 0 schema-validation errors,
0 patient requests left without a feasible carer, and 0 double-up members left without one.

## Setup for a new machine

```bash
git clone https://github.com/HodaBinaei/homehealthscheduler.git
cd homehealthscheduler
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
mkdir -p data data_today output today
```

`PROJECT_ROOT` is derived from the script's own location, so the pipeline
works from any directory. Override with `HHS_PROJECT_ROOT` if needed.

## Data (NOT in this repo)

These contain client and carer personal data and are excluded by
`.gitignore`. Obtain them through Caremark's approved internal channel —
never email them, never commit them.

Place in `data/`:
- VisitExport.csv
- users-new.json
- clients-new.json
- driving_data.json

`data_today/`, `today/` and `output/` are populated by the pipeline.
