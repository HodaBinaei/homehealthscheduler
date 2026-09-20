# Panel ↔ homehealthscheduler API contract

Panel (`server`) talks to this bridge with `X-API-Key: $BRIDGE_API_KEY`.

## Submit jobs

| Panel intent | Method & path | Body |
|--------------|---------------|------|
| Multi-schedule (primary) | `POST /api-data/v1/multi-schedule` | `{ "date", "hour", "visitIds": UUID[], "providerUserIds": number[] }` |
| Full-assignment (legacy / tools) | `POST /api-data/v1/schedule` | `{ "date", "hour" }` |
| Reschedule (whole day) | `POST /api-data/v1/optimize` | `{ "date", "hour" }` |

`date` and `hour` always come from the Panel request. Patients are loaded from
`roster_visit` for that `roster.date` with status **UNALLOCATED** or **ALLOCATED**
(same set as the map / unallocated basket). CANCELLED and other dates are excluded.

### Multi-schedule subset

- `visitIds` — Roster visit UUIDs for that date (same as suggest `visitIds`). **Required, non-empty.**
- `providerUserIds` — Panel caregiver user ids (same as suggest). **Required, non-empty.**
- Bridge maps visits → engine `prid`s via `client_schedule_id` + `slot_index`, filters caregivers by `cid`, then submits multicpsat.
- `400` if either list is empty, visits are missing/wrong date/cancelled/ad-hoc, or the filter yields zero carers/patients.

Success: `202` `{ status, date, job_id, job_type, run_id }` — returned **immediately**.
Payload build, S3 upload, and engine submit run in the background.
`job_id` is a stable bridge token for polling/logs (not the engine id until submit finishes).

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

When `result` is a Schedule payload, the bridge also normalizes it for Panel:

- Coerces `cid` / `crid` / `prid` / list entries to integers
- Adds Panel aliases alongside engine names:
  - `assigned_prids` ↔ `assigned_prid_list`
  - `unassigned_prids` ↔ `unassigned_prid_list`
  - `removed_caregivers` ↔ `removed_crid_list`

### PRID / CRID contract

- **prid** — ephemeral patient-request index for one job payload. Assigned densely
  `1…N` from that date’s UNALLOCATED+ALLOCATED roster visits
  (`ORDER BY client_schedule_id, slot_index, id`). Temporary schedules export under
  their `source_schedule_id`.
- **crid** — ephemeral caregiver-request / availability-segment index for the same payload.
- **cid** / **pid** — stable Panel user / client ids.

Panel ingest must remap results with the same visit-based PRID enumeration (not a
schedule-only export).

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
