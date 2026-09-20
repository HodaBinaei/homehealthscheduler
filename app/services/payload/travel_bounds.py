"""Clamp travel distance/time to the engine worker's hhs.DistanceItem bounds.

DB rows often contain duration_minutes=1440 (24h) for unreachable OSRM pairs.
The worker rejects anything above ENGINE_MAX_TRAVEL_MINUTES (600).
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.payload.constants import (
    ENGINE_MAX_DISTANCE_KM,
    ENGINE_MAX_TRAVEL_MINUTES,
)

logger = logging.getLogger("hhs.travel_bounds")

MAX_TRAVEL_MINUTES = int(ENGINE_MAX_TRAVEL_MINUTES)
MAX_DISTANCE_KM = float(ENGINE_MAX_DISTANCE_KM)


def coerce_travel_minutes(value: Any) -> int | None:
    """Parse a duration; return None if missing/invalid."""
    if value is None:
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def coerce_distance_km(value: Any) -> float | None:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:
        return None
    return n


def clamp_travel_minutes(value: Any, *, default: int = 0) -> int:
    n = coerce_travel_minutes(value)
    if n is None:
        return default
    return max(0, min(MAX_TRAVEL_MINUTES, n))


def clamp_distance_km(value: Any, *, default: float = 0.0) -> float:
    n = coerce_distance_km(value)
    if n is None:
        return default
    return max(0.0, min(MAX_DISTANCE_KM, n))


def scrub_engine_distance_matrices(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Final pass before engine submit: every distance_minute/distance_km is in bounds.

    Mutates and returns ``payload``. Raises if anything remains out of range.
    """
    clamped = 0
    for key in ("walking_data", "cycling_data", "driving_data"):
        matrix = payload.get(key)
        if not isinstance(matrix, dict):
            payload[key] = {"distances": {}}
            continue
        raw = matrix.get("distances") or {}
        if not isinstance(raw, dict):
            payload[key] = {"distances": {}}
            continue
        cleaned: dict[str, dict[str, Any]] = {}
        for pair_key, item in raw.items():
            if not isinstance(item, dict):
                continue
            from_id = str(item.get("from_location_id") or "")
            to_id = str(item.get("to_location_id") or "")
            expected = f"{from_id}_{to_id}"
            if not from_id or not to_id or str(pair_key) != expected:
                continue
            min_raw = item.get("distance_minute")
            km_raw = item.get("distance_km")
            minutes = clamp_travel_minutes(min_raw, default=0)
            km = clamp_distance_km(km_raw, default=0.0)
            if coerce_travel_minutes(min_raw) is not None and int(float(min_raw)) > MAX_TRAVEL_MINUTES:
                clamped += 1
            if coerce_distance_km(km_raw) is not None and float(km_raw) > MAX_DISTANCE_KM:
                clamped += 1
            cleaned[expected] = {
                "from_location_id": from_id,
                "to_location_id": to_id,
                "distance_km": km,
                "distance_minute": minutes,
            }
        payload[key] = {"distances": cleaned}

    # Fail closed: never ship an out-of-range minute to the worker.
    for key in ("walking_data", "cycling_data", "driving_data"):
        for pair_key, item in (payload.get(key) or {}).get("distances", {}).items():
            m = item.get("distance_minute")
            if not isinstance(m, int) or m < 0 or m > MAX_TRAVEL_MINUTES:
                raise ValueError(
                    f"distance_minute out of range after scrub: {key}.{pair_key}={m!r}"
                )

    if clamped:
        logger.warning(
            "Scrubbed engine distance matrices: clamped=%s (max_minute=%s max_km=%s)",
            clamped,
            MAX_TRAVEL_MINUTES,
            MAX_DISTANCE_KM,
        )
    return payload
