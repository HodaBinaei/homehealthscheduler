"""Serialize engine payloads for human inspection (Python literals)."""

from __future__ import annotations

import pprint
from typing import Any


def format_payload_python(payload: dict[str, Any]) -> str:
    """
    Render a payload the way offline pipeline day-files look: None / True / False
    instead of JSON null / true / false.
    """
    return pprint.pformat(payload, width=120, sort_dicts=False) + "\n"
