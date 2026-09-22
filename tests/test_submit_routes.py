from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def _client() -> TestClient:
    get_settings.cache_clear()
    return TestClient(app)


def test_optimize_and_aliases_return_202():
    accepted = {
        "status": "accepted",
        "date": "2026-09-22",
        "job_id": "job-1",
        "job_type": "reschedule",
        "run_id": "run-1",
        "hour": 8,
        "visit_ids": [],
        "provider_user_ids": [],
    }
    key = get_settings().api_key
    with patch(
        "app.api.routes.optimize.accept_scheduler_job",
        return_value=accepted,
    ):
        client = _client()
        for path in (
            "/api-data/v1/optimize",
            "/api-data/v1/optimize/",
            "/api-data/v1/reschedule",
            "/api/v1/optimize",
        ):
            response = client.post(
                path,
                json={"date": "2026-09-22", "hour": 8},
                headers={"X-API-Key": key},
            )
            assert response.status_code == 202, (path, response.status_code, response.text)
            body = response.json()
            assert body["job_id"] == "job-1"
            assert body["job_type"] == "reschedule"


def test_schedule_and_multi_schedule_return_202():
    key = get_settings().api_key
    with patch(
        "app.api.routes.schedule.accept_scheduler_job",
        return_value={
            "status": "accepted",
            "date": "2026-09-22",
            "job_id": "job-s",
            "job_type": "full-assignment",
            "run_id": "run-s",
            "hour": 8,
            "visit_ids": [],
            "provider_user_ids": [],
        },
    ):
        with patch(
            "app.api.routes.multi_schedule.accept_scheduler_job",
            return_value={
                "status": "accepted",
                "date": "2026-09-22",
                "job_id": "job-m",
                "job_type": "multicpsat",
                "run_id": "run-m",
                "hour": 8,
                "visit_ids": ["v1"],
                "provider_user_ids": [1],
            },
        ):
            client = _client()
            schedule = client.post(
                "/api-data/v1/schedule",
                json={"date": "2026-09-22", "hour": 8},
                headers={"X-API-Key": key},
            )
            assert schedule.status_code == 202, schedule.text
            assert schedule.json()["job_type"] == "full-assignment"

            multi = client.post(
                "/api-data/v1/multi-schedule",
                json={
                    "date": "2026-09-22",
                    "hour": 8,
                    "visitIds": ["11111111-1111-1111-1111-111111111111"],
                    "providerUserIds": [42],
                },
                headers={"X-API-Key": key},
            )
            assert multi.status_code == 202, multi.text
            assert multi.json()["job_type"] == "multicpsat"


def test_engine_api_root_avoids_double_prefix():
    get_settings.cache_clear()
    with patch.dict(
        "os.environ",
        {"ENGINE_BASE_URL": "http://engine.example/engine-api"},
        clear=False,
    ):
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.engine_reschedule_url.endswith(
            "/engine-api/api/v1/scheduler/reschedule"
        )
        assert "/engine-api/engine-api/" not in settings.engine_reschedule_url
