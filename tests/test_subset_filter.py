"""Unit tests for multi-schedule subset filtering."""

from __future__ import annotations

import pytest

from app.services.payload.subset_filter import filter_day_bundle_by_selection


def _bundle() -> dict:
    return {
        "date": "2026-09-17",
        "caregivers": {
            "1": {"cid": "10", "crid": "1", "location_id": "10"},
            "2": {"cid": "11", "crid": "2", "location_id": "11"},
        },
        "patients": {
            "1": {"pid": "20", "prid": "100", "location_id": "20"},
            "2": {"pid": "21", "prid": "101", "location_id": "21"},
        },
        "crid_prid_feasible": [
            {"crid": "1", "prid": "100", "weight": 1.0},
            {"crid": "1", "prid": "101", "weight": 0.5},
            {"crid": "2", "prid": "100", "weight": 0.8},
            {"crid": "2", "prid": "101", "weight": 0.9},
        ],
        "walking_data": {
            "distance": {"10_20": 1.0, "10_21": 2.0, "11_20": 1.5, "11_21": 2.5},
            "duration": {"10_20": 10, "10_21": 12, "11_20": 11, "11_21": 13},
        },
        "cycling_data": {"distance": {}, "duration": {}},
        "driving_data": {
            "distance": {"10_20": 1.0, "11_21": 2.0},
            "duration": {"10_20": 5, "11_21": 6},
        },
        "roster": {},
    }


def test_filter_keeps_selected_carers_and_prids():
    out = filter_day_bundle_by_selection(
        _bundle(), provider_user_ids=[10], prids=[100]
    )
    assert list(out["caregivers"]) == ["1"]
    assert list(out["patients"]) == ["1"]
    assert out["crid_prid_feasible"] == [{"crid": "1", "prid": "100", "weight": 1.0}]
    assert set(out["walking_data"]["distance"]) == {"10_20"}
    assert set(out["driving_data"]["distance"]) == {"10_20"}


def test_filter_rejects_empty_selection():
    with pytest.raises(ValueError, match="providerUserIds"):
        filter_day_bundle_by_selection(_bundle(), provider_user_ids=[], prids=[100])
    with pytest.raises(ValueError, match="visitIds"):
        filter_day_bundle_by_selection(
            _bundle(), provider_user_ids=[10], prids=[]
        )


def test_filter_rejects_no_matches():
    with pytest.raises(ValueError, match="No caregivers"):
        filter_day_bundle_by_selection(
            _bundle(), provider_user_ids=[999], prids=[100]
        )
    with pytest.raises(ValueError, match="No patients"):
        filter_day_bundle_by_selection(
            _bundle(), provider_user_ids=[10], prids=[999]
        )
