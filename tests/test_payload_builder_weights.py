from __future__ import annotations

from app.services.payload.constants import (
    MUST_SOURCE_COORDINATOR,
    MUST_SOURCE_HISTORICAL,
    REQUEST_WINDOW_PRIORITY_COORDINATOR,
    REQUEST_WINDOW_PRIORITY_HISTORICAL,
    ROSTER_HARD_EXCLUDE_WEIGHT,
    ROSTER_MUST_VISIT_WEIGHT,
    ROSTER_ONLY_VISIT_WEIGHT,
)
from app.services.payload.wire_shape import (
    aggregate_request_window_priority,
    fold_must_only_into_feasible,
    to_wire_caregiver,
    to_wire_patient,
)


def test_coordinator_must_gets_priority_1():
    specs = [
        {
            "crid": 1,
            "only_set": [],
            "must_visit_patients": {"10": 1.0},
            "must_visit_sources": {"10": MUST_SOURCE_COORDINATOR},
            "only_set_sources": {},
        }
    ]
    assert aggregate_request_window_priority(10, specs) == REQUEST_WINDOW_PRIORITY_COORDINATOR


def test_historical_must_gets_priority_0_01():
    specs = [
        {
            "crid": 1,
            "only_set": [],
            "must_visit_patients": {"10": 1.0},
            "must_visit_sources": {"10": MUST_SOURCE_HISTORICAL},
            "only_set_sources": {},
        }
    ]
    assert aggregate_request_window_priority(10, specs) == REQUEST_WINDOW_PRIORITY_HISTORICAL


def test_coordinator_only_gets_weight_2_and_zeros_others():
    feasible = [
        {"crid": 1, "prid": 10, "weight": 0.5},
        {"crid": 1, "prid": 11, "weight": 0.7},
        {"crid": 2, "prid": 10, "weight": 0.4},
    ]
    specs = [
        {
            "crid": 1,
            "only_set": [10],
            "must_visit_patients": {},
            "must_visit_sources": {},
            "only_set_sources": {"10": MUST_SOURCE_COORDINATOR},
        }
    ]
    folded = fold_must_only_into_feasible(feasible, specs)
    by_pair = {(r["crid"], r["prid"]): r["weight"] for r in folded}
    assert by_pair[(1, 10)] == ROSTER_ONLY_VISIT_WEIGHT
    assert by_pair[(1, 11)] == ROSTER_HARD_EXCLUDE_WEIGHT
    assert by_pair[(2, 10)] == 0.4


def test_historical_only_priority_and_weight():
    specs = [
        {
            "crid": 1,
            "only_set": [10],
            "must_visit_patients": {},
            "must_visit_sources": {},
            "only_set_sources": {"10": MUST_SOURCE_HISTORICAL},
        }
    ]
    assert aggregate_request_window_priority(10, specs) == REQUEST_WINDOW_PRIORITY_HISTORICAL
    folded = fold_must_only_into_feasible(
        [{"crid": 1, "prid": 10, "weight": 0.5}], specs
    )
    assert folded[0]["weight"] == ROSTER_ONLY_VISIT_WEIGHT


def test_must_gets_weight_1():
    folded = fold_must_only_into_feasible(
        [{"crid": 1, "prid": 10, "weight": 0.3}],
        [
            {
                "crid": 1,
                "only_set": [],
                "must_visit_patients": {"10": ROSTER_MUST_VISIT_WEIGHT},
                "must_visit_sources": {"10": MUST_SOURCE_COORDINATOR},
                "only_set_sources": {},
            }
        ],
    )
    assert folded[0]["weight"] == ROSTER_MUST_VISIT_WEIGHT


def test_ordinary_pair_unchanged_when_no_must_only():
    folded = fold_must_only_into_feasible(
        [{"crid": 1, "prid": 10, "weight": 0.55}],
        [{"crid": 1, "only_set": [], "must_visit_patients": {}, "must_visit_sources": {}, "only_set_sources": {}}],
    )
    assert folded[0]["weight"] == 0.55


def test_wire_patient_has_nested_request_window(ordinary_patient):
    wire = to_wire_patient(ordinary_patient, request_window_priority=1.0)
    assert "request_window" in wire
    rw = wire["request_window"]
    assert rw["start_time_hard"] <= rw["start_time_soft"]
    assert rw["end_time_hard"] >= rw["end_time_soft"]
    assert rw["start_time_soft"] - rw["start_time_hard"] >= 15
    assert rw["end_time_hard"] - rw["end_time_soft"] >= 15
    assert "must_visit_patients" not in wire
    assert "only_set" not in wire


def test_wire_caregiver_has_shift_not_only_set():
    wire = to_wire_caregiver(
        {
            "crid": 1,
            "cid": 9,
            "start_time": 480,
            "end_time": 960,
            "gender": "MALE",
            "travel_method": "DRIVING",
            "latitude": 51.5,
            "longitude": -0.1,
            "do_extend_feasiblity": True,
        }
    )
    assert "shift" in wire
    assert wire["shift"]["start_time"] == 480
    assert "only_set" not in wire
    assert "must_visit_patients" not in wire
    assert "dislike_set" not in wire
