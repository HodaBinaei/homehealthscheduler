# Panel ↔ homehealthscheduler API contract

Panel (`server`) talks to this bridge with `X-API-Key: $BRIDGE_API_KEY`.

## Submit jobs

| Panel intent | Method & path | Body |
|--------------|---------------|------|
| Multi-schedule (primary) | `POST /api-data/v1/multi-schedule` | `{ "date", "hour", "visitIds": UUID[], "providerUserIds": number[] }` |
| Full-assignment (legacy / tools) | `POST /api-data/v1/schedule` | `{ "date", "hour" }` |
| Reschedule (whole day) | `POST /api-data/v1/optimize` | `{ "date", "hour" }` |

### Multi-schedule subset

- `visitIds` — Roster visit UUIDs for that date (same as suggest `visitIds`). **Required, non-empty.**
- `providerUserIds` — Panel caregiver user ids (same as suggest). **Required, non-empty.**
- Bridge maps visits → engine `prid`s via `client_schedule_id` + `slot_index`, filters caregivers by `cid`, then submits multicpsat.
- `400` if either list is empty, visits are missing/wrong date/cancelled/ad-hoc, or the filter yields zero carers/patients.

Success: `202` `{ status, date, job_id, job_type, run_id }`.

## Job status

`GET /api-data/v1/jobs/{job_id}` proxies engine-service and returns:

```json
{
  "job_id": "...",
  "job_type": "multicpsat",
  "status": "queued|running|completed|failed",
  "result": null,
  "error": null,
  "created_at": "...",
  "started_at": "...",
  "completed_at": "...",
  "progress_percent": 0,
  "progress_message": "queued"
}
```

`progress_percent` is `0–100` (stage-based from engine-service). May be `null` on older workers.

## Job logs

### HTTP backlog

`GET /api-data/v1/jobs/{job_id}/logs?after={seq}` proxies engine-service:

```json
{
  "lines": [{ "seq": 1, "line": "Job queued (type=full-assignment)", "ts": "..." }],
  "nextSeq": 1
}
```

### Live WebSocket

`WS /api-data/v1/jobs/{job_id}/logs/ws` — same `X-API-Key` (header) or `?api_key=` query.

Upstream is engine-service `WS …/engine-api/api/v1/jobs/{job_id}/logs/ws`. Frames are forwarded as-is:

```json
{ "type": "log", "jobId": "...", "seq": 1, "line": "...", "ts": "..." }
{ "type": "status", "jobId": "...", "status": "running", "progressPercent": 5, "progressMessage": "running" }
{ "type": "done", "jobId": "...", "status": "completed" }
```

Deploy proxies must allow WebSocket upgrade on this path.
