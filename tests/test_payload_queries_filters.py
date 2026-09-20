from __future__ import annotations

import inspect

from app.services.payload import queries


def test_load_client_schedules_filters_is_suspend():
    source = inspect.getsource(queries.load_client_schedules)
    assert "csp.is_suspend = false" in source
    assert "csp.not_send_to_engine = false" in source


def test_load_active_clients_and_users_filter_not_send_to_engine():
    clients_src = inspect.getsource(queries.load_active_clients)
    users_src = inspect.getsource(queries.load_active_users)
    assert "not_send_to_engine = false" in clients_src
    assert "not_send_to_engine = false" in users_src


def test_load_user_availabilities_filters_not_send_to_engine():
    source = inspect.getsource(queries.load_user_availabilities)
    assert "uap.not_send_to_engine = false" in source


def test_load_day_engine_visits_only_unallocated_and_allocated_for_date():
    source = inspect.getsource(queries.load_day_engine_visits)
    assert "r.date = :date" in source
    assert "UNALLOCATED" in source
    assert "ALLOCATED" in source
    assert "CANCELLED" not in source or "status IN ('UNALLOCATED', 'ALLOCATED')" in source
    assert "CLIENT" in source
    assert "client_schedule_id IS NOT NULL" in source


def test_collect_client_export_records_uses_day_engine_visits():
    source = inspect.getsource(
        __import__(
            "app.services.payload.clients", fromlist=["collect_client_export_records"]
        ).collect_client_export_records
    )
    assert "load_day_engine_visits" in source
    assert "load_active_clients" not in source
