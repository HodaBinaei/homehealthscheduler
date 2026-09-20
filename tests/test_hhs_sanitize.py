from __future__ import annotations

from hhs import Distances, MAXIMUM_TRAVEL_TIME_BETWEEN_LOCATIONS_MINUTES

from app.services.payload.engine_adapter import reshape_distance_data, to_full_assignment_request
from app.services.payload.hhs_sanitize import sanitize_distance_data, sanitize_engine_request


def test_reshape_clamps_distance_minute_to_hhs_max():
    matrix = {
        "distance": {"1_2": 10.0},
        "duration": {"1_2": 1440},
    }
    out = reshape_distance_data(matrix)
    item = out["distances"]["1_2"]
    assert item["distance_minute"] == MAXIMUM_TRAVEL_TIME_BETWEEN_LOCATIONS_MINUTES
    Distances.model_validate(out)


def test_sanitize_distance_data_clamps_and_drops_bad_keys():
    raw = {
        "distances": {
            "1_2": {
                "from_location_id": "1",
                "to_location_id": "2",
                "distance_km": 5000.0,
                "distance_minute": 1440,
            },
            "bad": {
                "from_location_id": "1",
                "to_location_id": "2",
                "distance_km": 1.0,
                "distance_minute": 1,
            },
        }
    }
    out = sanitize_distance_data(raw)
    assert "bad" not in out["distances"]
    item = out["distances"]["1_2"]
    assert item["distance_minute"] == 600
    assert item["distance_km"] == 1000.0
    Distances.model_validate(out)


def test_full_assignment_payload_passes_hhs_distances():
    bundle = {
        "date": "2026-09-20",
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
        "walking_data": {"distance": {}, "duration": {}},
        "cycling_data": {"distance": {}, "duration": {}},
        "driving_data": {
            "distance": {"10_20": 2.0},
            "duration": {"10_20": 1440},
        },
        "roster": {},
    }
    body = to_full_assignment_request(bundle)
    assert body["driving_data"]["distances"]["10_20"]["distance_minute"] == 600
    assert body["patient_dict"][0]["gender_preference"] == 3
    assert body["caregiver_dict"][0]["gender_preference"] == 3
    Distances.model_validate(body["driving_data"])
    # sanitize_engine_request is idempotent
    again = sanitize_engine_request(body)
    assert again["driving_data"]["distances"]["10_20"]["distance_minute"] == 600
