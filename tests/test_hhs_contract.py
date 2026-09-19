from __future__ import annotations

from hhs import Caregiver, FeasibilityPair, Patient

from app.services.payload.constants import MUST_SOURCE_COORDINATOR, MUST_SOURCE_HISTORICAL
from app.services.payload.window_helpers import apply_roster_patient_specification
from app.services.payload.wire_shape import (
    aggregate_request_window_priority,
    fold_must_only_into_feasible,
    to_wire_caregiver,
    to_wire_feasible,
    to_wire_patient,
)


def test_wire_patient_validates_against_hhs(ordinary_patient):
    wire = to_wire_patient(ordinary_patient, request_window_priority=1.0)
    patient = Patient(**wire)
    assert patient.prid == "10"
    assert patient.request_window.request_window_priority == 1.0
    assert patient.request_window.start_time_soft - patient.request_window.start_time_hard >= 15
    assert patient.request_window.end_time_hard - patient.request_window.end_time_soft >= 15


def test_pinned_patient_validates_against_hhs():
    patient = {
        "pid": 1,
        "prid": 10,
        "start_time": 540,
        "end_time": 600,
        "duration": 60,
        "min_duration": 60,
        "gender": "FEMALE",
        "latitude": 51.5,
        "longitude": -0.1,
        "do_extend_feasiblity": False,
        "match_request": None,
        "fix_window": 0,
        "requested_start_time": 540,
        "requested_end_time": 600,
    }
    source = {
        "requested_start_time": 540,
        "requested_end_time": 600,
        "history_start": 540,
        "history_end": 600,
        "min_duration": 60,
    }
    pinned = apply_roster_patient_specification(
        patient,
        source,
        {"start_minute": 540, "end_minute": 600, "pinned": True},
    )
    wire = to_wire_patient(pinned, request_window_priority=0.01)
    model = Patient(**wire)
    assert model.request_window.duration == 60
    assert model.request_window.duration - model.request_window.min_duration >= 10
    assert model.request_window.start_time_soft == 540
    assert model.request_window.end_time_soft == 600
    assert model.request_window.start_time_hard == 525
    assert model.request_window.end_time_hard == 615


def test_matched_pinned_patient_uses_5_minute_margin_in_hhs():
    patient = {
        "pid": 1,
        "prid": 10,
        "start_time": 600,
        "end_time": 630,
        "duration": 30,
        "min_duration": 20,
        "gender": "MALE",
        "latitude": 51.5,
        "longitude": -0.1,
        "do_extend_feasiblity": False,
        "match_request": [11],
        "fix_window": 0,
        "requested_start_time": 600,
        "requested_end_time": 630,
    }
    source = {
        "requested_start_time": 600,
        "requested_end_time": 630,
        "history_start": 600,
        "history_end": 630,
        "min_duration": 20,
    }
    pinned = apply_roster_patient_specification(
        patient,
        source,
        {"start_minute": 600, "end_minute": 630, "pinned": True},
        matched=True,
    )
    wire = to_wire_patient(pinned, request_window_priority=1.0)
    model = Patient(**wire)
    assert model.request_window.start_time_soft - model.request_window.start_time_hard >= 5
    assert model.request_window.end_time_hard - model.request_window.end_time_soft >= 5
    assert model.request_window.match_request_list == ["11"]


def test_wire_caregiver_validates_against_hhs():
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
    caregiver = Caregiver(**wire)
    assert caregiver.crid == "1"
    assert caregiver.shift.start_time == 480
    assert caregiver.travel_mode.value == "driving"


def test_feasible_weights_validate_against_hhs():
    folded = fold_must_only_into_feasible(
        [
            {"crid": 1, "prid": 10, "weight": 0.5},
            {"crid": 1, "prid": 11, "weight": 0.7},
        ],
        [
            {
                "crid": 1,
                "only_set": [10],
                "must_visit_patients": {},
                "must_visit_sources": {},
                "only_set_sources": {"10": MUST_SOURCE_COORDINATOR},
            }
        ],
    )
    wire = to_wire_feasible(folded)
    models = [FeasibilityPair(**row) for row in wire]
    by_pair = {(m.crid, m.prid): m.weight for m in models}
    assert by_pair[("1", "10")] == 2.0
    assert by_pair[("1", "11")] == 0.0


def test_coordinator_beats_historical_for_priority():
    specs = [
        {
            "crid": 1,
            "only_set": [],
            "must_visit_patients": {"10": 1.0},
            "must_visit_sources": {"10": MUST_SOURCE_HISTORICAL},
            "only_set_sources": {},
        },
        {
            "crid": 2,
            "only_set": [],
            "must_visit_patients": {"10": 1.0},
            "must_visit_sources": {"10": MUST_SOURCE_COORDINATOR},
            "only_set_sources": {},
        },
    ]
    assert aggregate_request_window_priority(10, specs) == 1.0
