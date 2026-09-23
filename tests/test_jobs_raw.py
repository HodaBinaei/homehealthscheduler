from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def _client() -> TestClient:
    get_settings.cache_clear()
    return TestClient(app)


ENGINE_SCHEDULE = {
    "date": "2026-09-22",
    "assigned_prid_list": ["5"],
    "unassigned_prid_list": [],
    "assigned_crid_list": ["1"],
    "unassigned_crid_list": [],
    "removed_prid_list": [],
    "removed_crid_list": [],
    "caregiver_schedules": {
        "1": {
            "cid": "42",
            "crid": "1",
            "visits": [{"prid": "5", "start_time": 60, "end_time": 120}],
        }
    },
}


def test_job_status_raw_skips_panel_normalization():
    key = get_settings().api_key
    engine_body = {
        "job_id": "engine-1",
        "job_type": "multicpsat",
        "status": "completed",
        "result": ENGINE_SCHEDULE,
        "error": None,
        "progress_percent": 100,
        "progress_message": "done",
    }
    run = {
        "token": "bridge-1",
        "status": "SUBMITTED",
        "engine_job_id": "engine-1",
        "job_type": "multicpsat",
    }

    with patch("app.api.routes.jobs.get_run_by_job_ref", return_value=run):
        with patch(
            "app.api.routes.jobs.get_job",
            new_callable=AsyncMock,
            return_value=engine_body,
        ):
            client = _client()
            raw = client.get(
                "/api-data/v1/jobs/bridge-1?raw=true",
                headers={"X-API-Key": key},
            )
            assert raw.status_code == 200, raw.text
            body = raw.json()
            assert body["engine_job_id"] == "engine-1"
            assert body["result"]["assigned_prid_list"] == ["5"]
            assert "assigned_prids" not in body["result"]
            assert body["result"]["caregiver_schedules"]["1"]["cid"] == "42"

            normalized = client.get(
                "/api-data/v1/jobs/bridge-1",
                headers={"X-API-Key": key},
            )
            assert normalized.status_code == 200, normalized.text
            nbody = normalized.json()
            assert nbody["result"]["assigned_prids"] == [5]
            assert nbody["result"]["caregiver_schedules"]["1"]["cid"] == 42
