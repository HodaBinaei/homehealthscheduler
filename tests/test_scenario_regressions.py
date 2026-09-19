from __future__ import annotations

from datetime import date

from app.services.payload.feasible import prefer_day_specific_weights
from app.services.payload.schedule_rules import find_schedule_occurrence_covering_date
from app.services.payload.window_helpers import (
    apply_roster_caregiver_specification,
    apply_roster_patient_specification,
)
from app.services.payload.users import _blocking_for_user


def _base_schedule(**overrides):
    sched = {
        "id": 1,
        "client_id": 10,
        "days": ["Monday"],
        "requested_start_time": "09:00",
        "requested_end_time": "10:00",
        "end_time_date_offset_days": 0,
        "requested_duration": 60,
        "start_date": date(2026, 1, 5),
        "end_date": None,
        "occurs_every": 1,
        "exceptions": [],
        "preferences": {},
    }
    sched.update(overrides)
    return sched


def test_a1_b8_cancel_drops_from_must_and_pin():
    only, must, sources, _ = apply_roster_caregiver_specification(
        only_set=[5],
        must_visit_patients={"5": 1.0, "6": 1.0},
        has_only_links=False,
        pinned_prids=[5],
        cancelled_prids={5},
        must_visit_sources={"5": "coordinator", "6": "coordinator"},
        only_set_sources={"5": "coordinator"},
    )
    assert 5 not in only
    assert "5" not in must
    assert "6" in must


def test_a3_b4_exception_excludes_original_day():
    monday = date(2026, 9, 14)  # Monday
    sched = _base_schedule(exceptions=[monday])
    assert find_schedule_occurrence_covering_date(sched, monday) is None


def test_a3_b4_temporary_override_appears_on_target_day():
    tuesday = date(2026, 9, 15)
    temp = _base_schedule(
        days=["Tuesday"],
        start_date=None,
        preferences={
            "is_temporary": True,
            "effective_date_from": tuesday,
            "effective_date_to": tuesday,
            "source_schedule_id": 99,
        },
    )
    assert find_schedule_occurrence_covering_date(temp, tuesday) == tuesday
    assert find_schedule_occurrence_covering_date(temp, date(2026, 9, 16)) is None


def test_a5_b9_temp_visit_same_day_only():
    today = date(2026, 9, 17)
    temp = _base_schedule(
        days=["Thursday"],
        preferences={
            "is_temporary": True,
            "effective_date_from": today,
            "effective_date_to": today,
        },
    )
    assert find_schedule_occurrence_covering_date(temp, today) == today
    assert find_schedule_occurrence_covering_date(temp, date(2026, 9, 24)) is None


def test_a6_b10_temp_worker_availability_shape():
    """Availability occurrence uses the same temp date bounds as schedules."""
    from app.services.payload.schedule_rules import find_availability_occurrence

    today = date(2026, 9, 17)
    avail = {
        "days": ["Thursday"],
        "start_time": "08:00",
        "end_time": "16:00",
        "end_time_date_offset_days": 0,
        "start_date": None,
        "end_date": None,
        "occurs_every": 1,
        "exceptions": [],
        "preferences": {
            "is_temporary": True,
            "is_unavailability": False,
            "effective_date_from": today,
            "effective_date_to": today,
        },
    }
    assert find_availability_occurrence(avail, today) == today
    assert find_availability_occurrence(avail, date(2026, 9, 18)) is None


def test_a7_guest_client_uses_active_filter_contract():
    import inspect
    from app.services.payload import queries

    source = inspect.getsource(queries.load_active_clients)
    assert "status = 'Active'" in source
    assert "not_send_to_engine = false" in source


def test_a9_b6_day_off_blocks_segment():
    target = date(2026, 9, 17)
    day_offs = [
        {
            "user_id": 1,
            "start_dt": "2026-09-17T09:00:00+00:00",
            "end_dt": "2026-09-17T12:00:00+00:00",
        }
    ]
    blocking = _blocking_for_user(1, target, day_offs, [])
    assert len(blocking) == 1
    start_m, end_m = blocking[0]
    assert start_m == 540
    assert end_m > start_m
    assert end_m >= 720


def test_a11_b5_recurring_availability_appears_on_weekday():
    from app.services.payload.schedule_rules import find_availability_occurrence

    thursday = date(2026, 9, 17)
    friday = date(2026, 9, 18)
    avail = {
        "days": ["Thursday"],
        "start_time": "08:00",
        "end_time": "16:00",
        "end_time_date_offset_days": 0,
        "start_date": date(2026, 1, 1),
        "end_date": None,
        "occurs_every": 1,
        "exceptions": [],
        "preferences": {},
    }
    assert find_availability_occurrence(avail, thursday) == thursday
    assert find_availability_occurrence(avail, friday) is None


def test_a10_pin_with_only_links_goes_to_only_set():
    only, must, sources, only_sources = apply_roster_caregiver_specification(
        only_set=[3],
        must_visit_patients={},
        has_only_links=True,
        pinned_prids=[7],
        cancelled_prids=set(),
        only_set_sources={"3": "coordinator"},
    )
    assert only == [3, 7]
    assert must == {}
    assert only_sources["7"] == "historical"


def test_a12_b13_roster_adjusted_window_sets_fix_window():
    patient = {
        "start_time": 540,
        "end_time": 660,
        "duration": 60,
        "min_duration": 50,
        "fix_window": 0,
    }
    source = {
        "requested_start_time": 540,
        "requested_end_time": 600,
        "history_start": 530,
        "history_end": 590,
        "min_duration": 50,
    }
    # Allocated times match requested but requested differs from history
    roster_visit = {"start_minute": 540, "end_minute": 600, "pinned": False}
    out = apply_roster_patient_specification(patient, source, roster_visit)
    assert out["fix_window"] == 1
    assert out["duration"] == 60


def test_b1_unallocated_does_not_force_pin_window():
    patient = {
        "start_time": 540,
        "end_time": 660,
        "duration": 60,
        "min_duration": 50,
        "fix_window": 0,
    }
    source = {
        "requested_start_time": 540,
        "requested_end_time": 600,
        "history_start": 540,
        "history_end": 600,
        "min_duration": 50,
    }
    # No roster_visit (UNALLOCATED contributes nothing) → unchanged
    out = apply_roster_patient_specification(patient, source, None)
    assert out is patient or out["fix_window"] == 0


def test_feasible_no_duplicate_keys():
    pairs = [
        {"cgid": 1, "client_id": 2, "day_of_week": None, "weight": 0.5},
        {"cgid": 1, "client_id": 2, "day_of_week": 1, "weight": 0.9},
    ]
    weights = prefer_day_specific_weights(pairs, day_index=1)
    assert weights[(1, 2)] == 0.9
    assert len(weights) == 1


def test_day_boundary_duration_clamp_constant():
    from app.services.payload.constants import CLIENT_EXPORT_END_OF_DAY_EXTEND_THRESHOLD

    assert CLIENT_EXPORT_END_OF_DAY_EXTEND_THRESHOLD == 1439
