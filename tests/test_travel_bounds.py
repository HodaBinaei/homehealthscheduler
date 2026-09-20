from __future__ import annotations

from app.services.payload.distances import populate_distance_matrix
from app.services.payload.travel_bounds import clamp_travel_minutes, scrub_engine_distance_matrices


def test_clamp_travel_minutes_caps_at_600():
    assert clamp_travel_minutes(1440) == 600
    assert clamp_travel_minutes(601) == 600
    assert clamp_travel_minutes(600) == 600
    assert clamp_travel_minutes(59) == 59


def test_populate_distance_matrix_caps_1440():
    matrix = populate_distance_matrix(
        [
            {
                "from_id": 1,
                "to_id": 2,
                "distance_meters": 5000,
                "duration_minutes": 1440,
            }
        ],
        {1, 2},
    )
    assert matrix["duration"]["1_2"] == 600
    assert matrix["duration"]["2_1"] == 600


def test_scrub_engine_distance_matrices_caps_minutes():
    payload = {
        "driving_data": {
            "distances": {
                "1_2": {
                    "from_location_id": "1",
                    "to_location_id": "2",
                    "distance_km": 3.0,
                    "distance_minute": 1440,
                }
            }
        },
        "walking_data": {"distances": {}},
        "cycling_data": {"distances": {}},
    }
    out = scrub_engine_distance_matrices(payload)
    assert out["driving_data"]["distances"]["1_2"]["distance_minute"] == 600
