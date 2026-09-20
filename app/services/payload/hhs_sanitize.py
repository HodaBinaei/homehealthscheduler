"""Temporarily coerce engine payloads to satisfy hhs.py field bounds before submit."""

from __future__ import annotations

import logging
from typing import Any

from hhs import (
    MAXIMUM_DISTANCE_BETWEEN_LOCATIONS_KM,
    MAXIMUM_TRAVEL_TIME_BETWEEN_LOCATIONS_MINUTES,
    MINIMUM_SHIFT_DURATION,
    MINIMUM_SHIFT_START,
    MAXIMUM_SHIFT_END,
    DURATION_MINIMUM,
    DURATION_MAXIMUM,
    PATIENT_EARLEST_REQUEST_TIME,
    PATIENT_LATEST_REQUEST_TIME,
    Caregiver,
    Distances,
    GenderPreference,
    Patient,
)

logger = logging.getLogger("hhs.sanitize")

_MAX_TRAVEL = MAXIMUM_TRAVEL_TIME_BETWEEN_LOCATIONS_MINUTES  # 600
_MAX_KM = MAXIMUM_DISTANCE_BETWEEN_LOCATIONS_KM  # 1000.0


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n:  # NaN
        return default
    return max(lo, min(hi, n))


def _sanitize_extend_feasibility(ef: dict[str, Any] | None) -> dict[str, Any]:
    src = dict(ef or {})
    return {
        "extend": bool(src.get("extend", False)),
        "max_distance_km": _clamp_float(src.get("max_distance_km", 5.0), 0.0, 100.0, 5.0),
        "max_time_minutes": _clamp_int(src.get("max_time_minutes", 20), 0, 120, 20),
        "max_distance_border_crossings_km": _clamp_int(
            src.get("max_distance_border_crossings_km", 10), 0, 50, 10
        ),
        "max_time_border_crossings_minutes": _clamp_int(
            src.get("max_time_border_crossings_minutes", 60), 0, 120, 60
        ),
    }


def _sanitize_request_window(rw: dict[str, Any] | None) -> dict[str, Any]:
    src = dict(rw or {})
    hard_start = _clamp_int(
        src.get("start_time_hard", 0),
        PATIENT_EARLEST_REQUEST_TIME,
        PATIENT_LATEST_REQUEST_TIME,
        0,
    )
    hard_end = _clamp_int(
        src.get("end_time_hard", hard_start + DURATION_MINIMUM),
        PATIENT_EARLEST_REQUEST_TIME,
        PATIENT_LATEST_REQUEST_TIME,
        hard_start + DURATION_MINIMUM,
    )
    if hard_end <= hard_start:
        hard_end = min(PATIENT_LATEST_REQUEST_TIME, hard_start + DURATION_MINIMUM)

    soft_start = _clamp_int(
        src.get("start_time_soft", hard_start),
        PATIENT_EARLEST_REQUEST_TIME,
        PATIENT_LATEST_REQUEST_TIME,
        hard_start,
    )
    soft_end = _clamp_int(
        src.get("end_time_soft", hard_end),
        PATIENT_EARLEST_REQUEST_TIME,
        PATIENT_LATEST_REQUEST_TIME,
        hard_end,
    )
    if soft_end <= soft_start:
        soft_end = min(PATIENT_LATEST_REQUEST_TIME, soft_start + DURATION_MINIMUM)

    duration = _clamp_int(src.get("duration", DURATION_MINIMUM), DURATION_MINIMUM, DURATION_MAXIMUM, 60)
    min_duration = _clamp_int(
        src.get("min_duration", max(DURATION_MINIMUM, duration - 10)),
        DURATION_MINIMUM,
        DURATION_MAXIMUM,
        max(DURATION_MINIMUM, duration - 10),
    )
    if duration - min_duration < 10:
        min_duration = max(DURATION_MINIMUM, duration - 10)
    if min_duration > duration:
        min_duration = max(DURATION_MINIMUM, duration - 10)

    # Soft window must fit duration (hhs RequestWindow validator)
    if soft_end - soft_start < duration:
        soft_end = min(PATIENT_LATEST_REQUEST_TIME, soft_start + duration)
    if soft_end - soft_start < duration:
        soft_start = max(PATIENT_EARLEST_REQUEST_TIME, soft_end - duration)

    return {
        **src,
        "start_time_hard": hard_start,
        "end_time_hard": hard_end,
        "start_time_soft": soft_start,
        "end_time_soft": soft_end,
        "duration": duration,
        "min_duration": min_duration,
        "duration_reduction_priority": _clamp_float(
            src.get("duration_reduction_priority", 0.3), 0.0, 1.0, 0.3
        ),
        "request_window_priority": _clamp_float(
            src.get("request_window_priority", 1.0), 0.0, 1.0, 1.0
        ),
        "soft_window_violation_level": _clamp_float(
            src.get("soft_window_violation_level", 0.5), 0.0, 1.0, 0.5
        ),
        "match_request_list": list(src.get("match_request_list") or []),
    }


def _sanitize_shift(shift: dict[str, Any] | None) -> dict[str, Any]:
    src = dict(shift or {})
    start = _clamp_int(src.get("start_time", 0), MINIMUM_SHIFT_START, MAXIMUM_SHIFT_END, 0)
    end = _clamp_int(
        src.get("end_time", start + MINIMUM_SHIFT_DURATION),
        MINIMUM_SHIFT_START,
        MAXIMUM_SHIFT_END,
        start + MINIMUM_SHIFT_DURATION,
    )
    if end - start < MINIMUM_SHIFT_DURATION:
        end = min(MAXIMUM_SHIFT_END, start + MINIMUM_SHIFT_DURATION)
    if end - start < MINIMUM_SHIFT_DURATION:
        start = max(MINIMUM_SHIFT_START, end - MINIMUM_SHIFT_DURATION)
    return {"start_time": start, "end_time": end}


def _sanitize_location(loc: dict[str, Any] | None) -> dict[str, Any]:
    src = dict(loc or {})
    return {
        "latitude": _clamp_float(src.get("latitude", 0.0), -90.0, 90.0, 0.0),
        "longitude": _clamp_float(src.get("longitude", 0.0), -180.0, 180.0, 0.0),
        "postcode": src.get("postcode"),
    }


def _default_gender_preference(raw: Any) -> int:
    try:
        n = int(raw)
        if n in (1, 2, 3):
            return n
    except (TypeError, ValueError):
        pass
    return int(GenderPreference.BOTH)


def sanitize_patient(patient: dict[str, Any]) -> dict[str, Any]:
    out = dict(patient)
    out["pid"] = str(out.get("pid") or "")
    out["prid"] = str(out.get("prid") or "")
    out["location_id"] = str(out.get("location_id") or out["pid"])
    out["gender"] = str(out.get("gender") or "male").lower()
    out["gender_preference"] = _default_gender_preference(out.get("gender_preference"))
    out["location"] = _sanitize_location(out.get("location"))
    out["request_window"] = _sanitize_request_window(out.get("request_window"))
    out["extend_feasibility"] = _sanitize_extend_feasibility(out.get("extend_feasibility"))
    return out


def sanitize_caregiver(caregiver: dict[str, Any]) -> dict[str, Any]:
    out = dict(caregiver)
    out["cid"] = str(out.get("cid") or "")
    out["crid"] = str(out.get("crid") or "")
    out["location_id"] = str(out.get("location_id") or out["cid"])
    out["current_location_id"] = str(out.get("current_location_id") or out["cid"])
    out["start_location_id"] = str(out.get("start_location_id") or out["cid"])
    out["end_location_id"] = str(out.get("end_location_id") or out["cid"])
    out["gender"] = str(out.get("gender") or "female").lower()
    out["gender_preference"] = _default_gender_preference(out.get("gender_preference"))
    mode = str(out.get("travel_mode") or "driving").lower()
    if mode not in {"driving", "walking", "bicycling", "public_transit"}:
        mode = "driving"
    out["travel_mode"] = mode
    out["location"] = _sanitize_location(out.get("location"))
    out["shift"] = _sanitize_shift(out.get("shift"))
    out["extend_feasibility"] = _sanitize_extend_feasibility(out.get("extend_feasibility"))
    out["caregiver_usage_priority"] = _clamp_float(
        out.get("caregiver_usage_priority", 0.5), 0.0, 1.0, 0.5
    )
    return out


def sanitize_distance_data(matrix: dict[str, Any] | None) -> dict[str, Any]:
    """Clamp distance_km / distance_minute to hhs DistanceItem bounds; drop bad keys."""
    if not matrix:
        return {"distances": {}}
    raw = matrix.get("distances") or {}
    out: dict[str, dict[str, Any]] = {}
    clamped = 0
    dropped = 0
    for key, item in raw.items():
        if not isinstance(item, dict):
            dropped += 1
            continue
        from_id = str(item.get("from_location_id") or "")
        to_id = str(item.get("to_location_id") or "")
        expected = f"{from_id}_{to_id}"
        if not from_id or not to_id or str(key) != expected:
            dropped += 1
            continue
        km_raw = item.get("distance_km")
        min_raw = item.get("distance_minute")
        km = _clamp_float(km_raw, 0.0, _MAX_KM, 0.0)
        minutes = _clamp_int(min_raw, 0, _MAX_TRAVEL, 0)
        if km_raw is not None and float(km_raw) > _MAX_KM:
            clamped += 1
        if min_raw is not None and int(min_raw) > _MAX_TRAVEL:
            clamped += 1
        out[expected] = {
            "from_location_id": from_id,
            "to_location_id": to_id,
            "distance_km": km,
            "distance_minute": minutes,
        }
    if clamped or dropped:
        logger.warning(
            "Sanitized distance matrix: clamped=%s dropped=%s kept=%s (max_minute=%s max_km=%s)",
            clamped,
            dropped,
            len(out),
            _MAX_TRAVEL,
            _MAX_KM,
        )
    return {"distances": out}


def sanitize_feasible_pairs(rows: list[Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "crid": str(row.get("crid") or ""),
                "prid": str(row.get("prid") or ""),
                "weight": _clamp_float(row.get("weight", 0.0), 0.0, 1.0, 0.0),
            }
        )
    return out


def _try_validate(label: str, model_cls: type, payload: dict[str, Any]) -> None:
    try:
        model_cls.model_validate(payload)
    except Exception as exc:
        logger.warning("hhs %s still invalid after sanitize: %s", label, exc)


def sanitize_engine_request(body: dict[str, Any]) -> dict[str, Any]:
    """
    Verify/fix payload fields against hhs.py bounds before engine submit.

    Temporary safety net for DB outliers (e.g. distance_minute=1440 > 600).
    """
    out = dict(body)

    patients = [sanitize_patient(p) for p in (out.get("patient_dict") or []) if isinstance(p, dict)]
    caregivers = [
        sanitize_caregiver(c) for c in (out.get("caregiver_dict") or []) if isinstance(c, dict)
    ]
    out["patient_dict"] = patients
    out["caregiver_dict"] = caregivers
    out["crid_prid_feasible_dict"] = sanitize_feasible_pairs(out.get("crid_prid_feasible_dict"))

    for key in ("walking_data", "cycling_data", "driving_data"):
        out[key] = sanitize_distance_data(out.get(key))

    for i, p in enumerate(patients):
        _try_validate(f"Patient[{i}]", Patient, p)
    for i, c in enumerate(caregivers):
        _try_validate(f"Caregiver[{i}]", Caregiver, c)
    for key in ("walking_data", "cycling_data", "driving_data"):
        _try_validate(key, Distances, out[key])

    return out
