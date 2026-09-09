# Home Health Scheduler

Offline Caremark history pipelines **and** a FastAPI bridge that builds Panel-compatible
engine execute payloads from PostgreSQL and submits them to the external engine.

---

## FastAPI service (engine bridge)

### Flow

1. Panel (or any client) calls `POST /api/v1/schedule/prepare-data` with `X-API-Key`, `date`, and `hour`.
2. The API returns **202 Accepted** immediately and continues in the background.
3. Background work loads carers, calls, feasible pairs, and distances from the **same PostgreSQL** as the Panel server.
4. It builds the Panel `EngineExecutePayload` shape, logs it to the terminal, and `POST`s it to `ENGINE_URL` (default `http://34.244.104.57/execute`).

`hour` is accepted and validated (`0–23`) but **unused in v1**.

### Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: API_KEY, DB_*, ENGINE_URL
```

### Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
# or: python -m app.main
```

### Example request

```bash
curl -X POST http://localhost:8000/api/v1/schedule/prepare-data \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me-to-a-long-secret" \
  -d '{"date":"2026-09-07","hour":8}'
```

Success response (`202`):

```json
{"status":"accepted","date":"2026-09-07"}
```

Health: `GET /health`, `GET /health/db`.

### Env vars

| Variable | Purpose |
|----------|---------|
| `API_KEY` | Static key expected in `X-API-Key` |
| `DB_HOST` / `DB_PORT` / `DB_USERNAME` / `DB_PASSWORD` / `DB_NAME` | Same DB as Panel |
| `ENGINE_URL` | Engine execute endpoint |
| `LOG_PAYLOAD` | `true` logs full JSON sent to the engine |
| `APP_HOST` / `APP_PORT` | Uvicorn bind (when using `python -m app.main`) |

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
