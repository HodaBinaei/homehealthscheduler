# Home Health Scheduler

Offline Caremark history pipelines **and** a FastAPI bridge that builds engine-service
scheduler payloads from PostgreSQL and submits full-assignment, multicpsat, and reschedule jobs.

Panel contract (what the server should send): see **[PANEL_API_CONTRACT.md](./PANEL_API_CONTRACT.md)**.

---

## FastAPI service (engine bridge)

### Flow

1. Panel calls one of:
   - `POST /api-data/v1/schedule` → engine `full-assignment`
   - `POST /api-data/v1/multi-schedule` → engine `multicpsat`
   - `POST /api-data/v1/optimize` → engine `reschedule`
   with `X-API-Key`, `date`, and `hour` only.
2. The bridge loads carers, calls, feasible pairs, distances (and for optimize, current roster allocations) from the **same PostgreSQL** as the Panel server.
3. It adapts the payload to engine-service DTOs, uploads to **S3** (if configured), and `POST`s to `{ENGINE_BASE_URL}/api/v1/scheduler/...` with `ENGINE_API_KEY`.
4. Response is **202** with `job_id`. Poll `GET /api-data/v1/jobs/{job_id}` (proxied to engine-service).
5. Run metadata (+ S3 key) are stored in `hhs_engine_runs` (`token` = engine `job_id`).

`hour` is accepted and validated (`0–23`) but **unused for payload build in v1**.

### Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: API_KEY, DB_*, ENGINE_BASE_URL, ENGINE_API_KEY
```

### Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
# or: python -m app.main
```

### Example request

```bash
curl -X POST http://localhost:8000/api-data/v1/schedule \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me-to-a-long-secret" \
  -d '{"date":"2026-09-07","hour":8}'
```

Success response (`202`):

```json
{"status":"accepted","date":"2026-09-07","job_id":"...","job_type":"full-assignment","run_id":"..."}
```

### List engine runs / job ids

```bash
curl http://localhost:8000/api-data/v1/schedule/runs \
  -H "X-API-Key: change-me-to-a-long-secret"

curl "http://localhost:8000/api-data/v1/schedule/runs?date=2026-09-07&limit=20" \
  -H "X-API-Key: change-me-to-a-long-secret"

curl http://localhost:8000/api-data/v1/schedule/runs/<job_id> \
  -H "X-API-Key: change-me-to-a-long-secret"
```

### Download payload from S3

```bash
curl http://localhost:8000/api-data/v1/schedule/runs/<job_id>/payload \
  -H "X-API-Key: change-me-to-a-long-secret"
# -> { "download_url": "https://...", "request_payload_s3_key": "...", ... }
```

Health: `GET /health`, `GET /health/db`.

### Env vars

| Variable | Purpose |
|----------|---------|
| `API_KEY` | Static key expected from Panel in `X-API-Key` |
| `DB_HOST` / `DB_PORT` / `DB_USERNAME` / `DB_PASSWORD` / `DB_NAME` | Same DB as Panel |
| `ENGINE_BASE_URL` | engine-service base URL (no path suffix) |
| `ENGINE_API_KEY` | Sent to engine-service as `X-API-Key` |
| `ENGINE_TIMEOUT_SECONDS` | HTTP timeout for engine calls (default `120`) |
| `LOG_PAYLOAD` | `true` logs full JSON sent to the engine |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` / `AWS_BUCKET_NAME` | S3 payload archive |
| `S3_PAYLOAD_PREFIX` | Key prefix (default `hhs/engine-payloads`) |
| `S3_PRESIGN_EXPIRES_SECONDS` | Download URL TTL (default `300`) |
| `APP_HOST` / `APP_PORT` | Uvicorn bind (when using `python -m app.main`) |

Legacy: if `ENGINE_BASE_URL` is unset, `ENGINE_URL` ending in `/execute` is stripped to derive the base URL.

---

## Offline pipeline (legacy scripts)

### Folder layout

```
your-project/
├── app/                                <- FastAPI engine bridge
├── run_all_in_one.py                   <- CSV-based "today" (Option A)
├── build_full_recompute_real_day.py    <- real day export (Option B)
├── hhs.py                              <- scheduling schema
├── data/                               <- history inputs (gitignored)
├── data_today/                         <- day-of inputs (gitignored)
└── output/                             <- generated files (gitignored)
```

### Option A: `run_all_in_one.py`

Supply `data_today/today_patients.csv` and `today_carers.csv`, set `TARGET_DATE`, then:

```bash
python3 run_all_in_one.py
```

### Option B: `build_full_recompute_real_day.py`

Place day export JSON under `data_today/`, then:

```bash
python3 build_full_recompute_real_day.py
```

### Data (NOT in this repo)

Obtain Caremark-approved files into `data/` — never commit PII.
