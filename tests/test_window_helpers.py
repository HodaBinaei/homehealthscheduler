from __future__ import annotations

from app.services.payload.constants import (
    MIN_MATCHED_PATIENT_WINDOW_SLACK_MINUTES,
    MIN_PATIENT_WINDOW_SLACK_MINUTES,
    MUST_SOURCE_HISTORICAL,
)
from app.services.payload.window_helpers import (
    apply_roster_caregiver_specification,
    apply_roster_patient_specification,
    pin_window_slack_minutes,
    widen_match_group_window,
    widen_patient_window,
)


def test_ordinary_window_slack_is_15():
    start, end = widen_patient_window(600, 650, 600, 660, duration=60)
    assert end - start >= 60 + MIN_PATIENT_WINDOW_SLACK_MINUTES
    assert MIN_PATIENT_WINDOW_SLACK_MINUTES == 15


def test_matched_group_slack_is_5():
    assert MIN_MATCHED_PATIENT_WINDOW_SLACK_MINUTES == 5
    start, end = widen_match_group_window(
        [
            {
                "prid": 1,
                "duration": 30,
                "requested_start_time": 600,
                "requested_end_time": 630,
                "history_start": 600,
                "history_end": 630,
            },
            {
                "prid": 2,
                "duration": 30,
                "requested_start_time": 605,
                "requested_end_time": 635,
                "history_start": 605,
                "history_end": 635,
            },
        ]
    )
    assert end - start >= 30 + MIN_MATCHED_PATIENT_WINDOW_SLACK_MINUTES


def test_pinned_visit_never_zero_width():
    patient = {
        "start_time": 0,
        "end_time": 0,
        "duration": 60,
        "min_duration": 60,
        "fix_window": 0,
    }
    source = {
        "requested_start_time": 540,
        "requested_end_time": 600,
        "history_start": 540,
        "history_end": 600,
        "min_duration": 60,
    }
    roster_visit = {"start_minute": 540, "end_minute": 600, "pinned": True}
    out = apply_roster_patient_specification(patient, source, roster_visit)
    assert out["end_time"] - out["start_time"] > 0
    margin = pin_window_slack_minutes(matched=False)
    assert out["start_time"] == 540 - margin
    assert out["end_time"] == 600 + margin
    assert out["_soft_start"] == 540
    assert out["_soft_end"] == 600
    assert out["min_duration"] == 50  # duration - 10


def test_pinned_matched_uses_5_minute_margin():
    patient = {"start_time": 0, "end_time": 0, "duration": 30, "fix_window": 0}
    source = {
        "requested_start_time": 600,
        "requested_end_time": 630,
        "history_start": 600,
        "history_end": 630,
        "min_duration": 30,
    }
    roster_visit = {"start_minute": 600, "end_minute": 630, "pinned": True}
    out = apply_roster_patient_specification(
        patient, source, roster_visit, matched=True
    )
    assert out["start_time"] == 595
    assert out["end_time"] == 635


def test_pin_adds_historical_must():
    only, must, sources, only_sources = apply_roster_caregiver_specification(
        only_set=[],
        must_visit_patients={},
        has_only_links=False,
        pinned_prids=[7],
        cancelled_prids=set(),
    )
    assert must["7"] == 1.0
    assert sources["7"] == MUST_SOURCE_HISTORICAL
    assert only == []
    assert only_sources == {}


def test_cancel_drops_pin_from_must():
    only, must, sources, _ = apply_roster_caregiver_specification(
        only_set=[],
        must_visit_patients={"7": 1.0},
        has_only_links=False,
        pinned_prids=[7],
        cancelled_prids={7},
        must_visit_sources={"7": MUST_SOURCE_HISTORICAL},
    )
    assert must == {}
    assert sources == {}
    assert only == []


def test_widen_already_wide_window_unchanged():
    start, end = widen_patient_window(500, 700, 540, 600, duration=60)
    assert (start, end) == (500, 700)
