from __future__ import annotations

import pytest

from app.services.payload.constants import (
    MIN_MATCHED_PATIENT_WINDOW_SLACK_MINUTES,
    MIN_PATIENT_WINDOW_SLACK_MINUTES,
    MUST_SOURCE_COORDINATOR,
    MUST_SOURCE_HISTORICAL,
    REQUEST_WINDOW_PRIORITY_COORDINATOR,
    REQUEST_WINDOW_PRIORITY_HISTORICAL,
    ROSTER_HARD_EXCLUDE_WEIGHT,
    ROSTER_MUST_VISIT_WEIGHT,
    ROSTER_ONLY_VISIT_WEIGHT,
)


@pytest.fixture
def ordinary_patient() -> dict:
    return {
        "pid": 1,
        "prid": 10,
        "start_time": 540,
        "end_time": 660,
        "requested_start_time": 555,
        "requested_end_time": 645,
        "duration": 60,
        "min_duration": 50,
        "gender": "FEMALE",
        "latitude": 51.5,
        "longitude": -0.1,
        "do_extend_feasiblity": False,
        "match_request": None,
    }


@pytest.fixture
def caregiver_spec_coordinator_must() -> dict:
    return {
        "crid": 1,
        "only_set": [],
        "must_visit_patients": {"10": ROSTER_MUST_VISIT_WEIGHT},
        "must_visit_sources": {"10": MUST_SOURCE_COORDINATOR},
        "only_set_sources": {},
    }


@pytest.fixture
def caregiver_spec_historical_must() -> dict:
    return {
        "crid": 1,
        "only_set": [],
        "must_visit_patients": {"10": ROSTER_MUST_VISIT_WEIGHT},
        "must_visit_sources": {"10": MUST_SOURCE_HISTORICAL},
        "only_set_sources": {},
    }
