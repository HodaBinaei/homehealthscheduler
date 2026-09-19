from __future__ import annotations

import inspect

from app.services.payload import queries


def test_load_client_schedules_filters_is_suspend():
    source = inspect.getsource(queries.load_client_schedules)
    assert "csp.is_suspend = false" in source
    assert "csp.not_send_to_engine = false" in source
