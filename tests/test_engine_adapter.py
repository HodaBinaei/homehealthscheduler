"""Unit tests for engine-service payload adapter."""

from __future__ import annotations

from app.services.payload.engine_adapter import (
    build_last_schedule_from_roster,
    reshape_distance_data,
    to_full_assignment_request,
    to_multicpsat_request,
    to_reschedule_request,
)


def _sample_bundle() -> dict:
    return {
        "date": "2026-09-17",
        "caregivers": {
            "1": {
                "cid": "10",
                "crid": "1",
                "gender": "female",
                "travel_mode": "driving",
                "location_id": "10",
                "start_location_id": "10",
                "end_location_id": "10",
                "current_location_id": "10",
                "location": {"latitude": 53.3, "longitude": -6.2, "postcode": None},
                "shift": {"start_time": 480, "end_time": 1020},
                "extend_feasibility": {
                    "extend": False,
                    "max_distance_km": 5.0,
                    "max_time_minutes": 20,
                    "max_distance_border_crossings_km": 10,
                    "max_time_border_crossings_minutes": 60,
                },
                "caregiver_usage_priority": 1.0,
            }
        },
        "patients": {
            "1": {
                "pid": "20",
                "prid": "100",
                "gender": "female",
                "location_id": "20",
                "location": {"latitude": 53.31, "longitude": -6.21, "postcode": None},
                "request_window": {
                    "start_time_hard": 540,
                    "end_time_hard": 720,
                    "start_time_soft": 540,
                    "end_time_soft": 660,
                    "duration": 60,
                    "min_duration": 45,
                    "duration_reduction_priority": 1.0,
                    "request_window_priority": 1.0,
                    "soft_window_violation_level": 1.0,
                    "match_request_list": [],
                },
                "extend_feasibility": {
                    "extend": False,
                    "max_distance_km": 5.0,
                    "max_time_minutes": 20,
                    "max_distance_border_crossings_km": 10,
                    "max_time_border_crossings_minutes": 60,
                },
            }
        },
        "crid_prid_feasible": [{"crid": "1", "prid": "100", "weight": 1.0}],
        "walking_data": {
            "distance": {"10_20": 1.5, "20_10": 1.5, "10_10": 0.0},
            "duration": {"10_20": 12, "20_10": 12, "10_10": 0},
        },
        "cycling_data": {"distance": {}, "duration": {}},
        "driving_data": {
            "distance": {"10_20": 2.0, "20_10": None},
            "duration": {"10_20": 8, "20_10": 8},
        },
        "roster": {
            "allocated_prids_by_caregiver_id": {10: [100]},
            "visit_by_prid": {100: {"start_minute": 540, "end_minute": 600, "pinned": False}},
            "cancelled_prids": set(),
            "pinned_prids_by_caregiver_id": {},
        },
    }


def test_reshape_distance_data_skips_nulls_and_builds_dto():
    out = reshape_distance_data(
        {
            "distance": {"10_20": 2.0, "20_10": None, "bad": 1.0},
            "duration": {"10_20": 8, "20_10": 8},
        }
    )
    assert set(out["distances"]) == {"10_20"}
    assert out["distances"]["10_20"] == {
        "from_location_id": "10",
        "to_location_id": "20",
        "distance_km": 2.0,
        "distance_minute": 8,
    }


def test_full_assignment_request_shape():
    body = to_full_assignment_request(_sample_bundle())
    assert body["data_day"] == "2026-09-17"
    assert isinstance(body["caregiver_dict"], list)
    assert len(body["caregiver_dict"]) == 1
    assert isinstance(body["patient_dict"], list)
    assert body["crid_prid_feasible_dict"][0]["prid"] == "100"
    assert "distances" in body["walking_data"]
    assert body["save_result_schedule"] is False
    assert "data" not in body
    assert "date_data" not in body


def test_multicpsat_has_no_day_field():
    body = to_multicpsat_request(_sample_bundle())
    assert "data_day" not in body
    assert "data_name" not in body
    assert len(body["caregiver_dict"]) == 1


def test_reschedule_includes_last_schedule():
    body = to_reschedule_request(_sample_bundle())
    assert body["data_name"] == "2026-09-17"
    assert body["rescheduling_meta_dict"] == {}
    last = body["last_schedule_dict"]
    assert last["date"] == "2026-09-17"
    assert "1" in last["caregiver_schedules"]
    visit = last["caregiver_schedules"]["1"]["visits"][0]
    assert visit["prid"] == "100"
    assert visit["start_time"] == 540
    assert visit["duration"] == 60


def test_build_last_schedule_empty_when_no_allocations():
    bundle = _sample_bundle()
    bundle["roster"] = {
        "allocated_prids_by_caregiver_id": {},
        "visit_by_prid": {},
        "cancelled_prids": set(),
        "pinned_prids_by_caregiver_id": {},
    }
    last = build_last_schedule_from_roster(bundle)
    assert last["caregiver_schedules"] == {}
    assert last["assigned_prid_list"] == []
    assert last["unassigned_prid_list"] == ["100"]
