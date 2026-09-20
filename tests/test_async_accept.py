from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from app.services.submit_job import (
    STATUS_BUILDING,
    STATUS_PENDING,
    accept_scheduler_job,
)


def test_accept_scheduler_job_returns_ids_without_building():
    captured: dict = {}

    def fake_insert(db, **kwargs):
        captured.update(kwargs)
        return {
            "id": kwargs["run_id"],
            "token": kwargs["token"],
            "status": kwargs["status"],
            "job_type": kwargs["job_type"],
        }

    with patch("app.services.submit_job.SessionLocal", return_value=MagicMock()):
        with patch("app.services.submit_job.insert_run", side_effect=fake_insert):
            with patch("app.services.submit_job.build_day_bundle_for_engine") as build:
                accepted = accept_scheduler_job(
                    job_type="full-assignment",
                    target_date="2026-09-20",
                    hour=8,
                )
                build.assert_not_called()

    assert accepted["status"] == "accepted"
    assert accepted["date"] == "2026-09-20"
    assert accepted["job_type"] == "full-assignment"
    assert accepted["job_id"]
    assert accepted["run_id"]
    assert accepted["job_id"] != accepted["run_id"]
    assert captured["status"] == STATUS_PENDING
    assert captured["token"] == accepted["job_id"]
    assert captured["roster_date"] == date(2026, 9, 20)
    assert captured["hour"] == 8


def test_accept_multicpsat_requires_selection():
    try:
        accept_scheduler_job(
            job_type="multicpsat",
            target_date="2026-09-20",
            hour=8,
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "visitIds" in str(exc)


def test_local_status_building_maps_to_running():
    from app.api.routes.jobs import _local_status_payload

    payload = _local_status_payload(
        {
            "token": "bridge-1",
            "status": STATUS_BUILDING,
            "job_type": "multicpsat",
            "created_at": "t0",
            "updated_at": "t1",
        },
        "bridge-1",
    )
    assert payload["status"] == "running"
    assert payload["progress_percent"] == 5
