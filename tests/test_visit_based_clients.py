from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from app.services.payload.clients import collect_client_export_records


def test_collect_builds_only_from_day_visits(monkeypatch):
    target = date(2026, 9, 20)
    visits = [
        {
            "id": "v1",
            "receiver_type": "CLIENT",
            "receiver_client_id": 10,
            "provider_user_id": None,
            "client_schedule_id": 100,
            "slot_index": 0,
            "start_minute": 540,
            "end_minute": 600,
            "window_end_offset_days": 0,
            "status": "UNALLOCATED",
            "pinned": False,
            "pending_window_start_minute": None,
            "pending_window_end_minute": None,
            "pending_min_duration": None,
        },
        {
            "id": "v2",
            "receiver_type": "CLIENT",
            "receiver_client_id": 11,
            "provider_user_id": 5,
            "client_schedule_id": 101,
            "slot_index": 0,
            "start_minute": 600,
            "end_minute": 660,
            "window_end_offset_days": 0,
            "status": "ALLOCATED",
            "pinned": True,
            "pending_window_start_minute": None,
            "pending_window_end_minute": None,
            "pending_min_duration": None,
        },
    ]
    clients = [
        {
            "id": 10,
            "name": "Ann",
            "lastname": "A",
            "gender": "Female",
            "latitude": 1.0,
            "longitude": 2.0,
            "extended_feasibility": False,
        },
        {
            "id": 11,
            "name": "Bob",
            "lastname": "B",
            "gender": "Male",
            "latitude": 3.0,
            "longitude": 4.0,
            "extended_feasibility": False,
        },
    ]
    schedules = [
        {
            "id": 100,
            "client_id": 10,
            "days": ["Saturday"],
            "requested_start_time": "09:00",
            "requested_end_time": "10:00",
            "end_time_date_offset_days": 0,
            "requested_duration": 60,
            "preferences": {},
            "exceptions": [],
        },
        {
            "id": 101,
            "client_id": 11,
            "days": ["Saturday"],
            "requested_start_time": "10:00",
            "requested_end_time": "11:00",
            "end_time_date_offset_days": 0,
            "requested_duration": 60,
            "preferences": {},
            "exceptions": [],
        },
    ]

    monkeypatch.setattr(
        "app.services.payload.queries.load_day_engine_visits",
        lambda db, d: visits if d == target else [],
    )
    monkeypatch.setattr(
        "app.services.payload.queries.load_clients_by_ids",
        lambda db, ids: [c for c in clients if c["id"] in ids],
    )
    monkeypatch.setattr(
        "app.services.payload.queries.load_client_schedules_by_ids",
        lambda db, ids: [s for s in schedules if s["id"] in ids],
    )
    monkeypatch.setattr(
        "app.services.payload.queries.load_active_clients",
        lambda db: (_ for _ in ()).throw(AssertionError("must not load all clients")),
    )

    bundle = collect_client_export_records(MagicMock(), target)
    assert len(bundle["entries"]) == 2
    assert {e["entry"]["pid"] for e in bundle["entries"]} == {10, 11}
    assert bundle["visits"] == visits


def test_collect_empty_when_no_day_visits(monkeypatch):
    monkeypatch.setattr(
        "app.services.payload.queries.load_day_engine_visits",
        lambda db, d: [],
    )
    bundle = collect_client_export_records(MagicMock(), date(2026, 9, 20))
    assert bundle["entries"] == []
    assert bundle["visits"] == []
