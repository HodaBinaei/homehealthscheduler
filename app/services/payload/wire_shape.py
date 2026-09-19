from __future__ import annotations

from typing import Any

from app.services.payload.constants import (
    DEFAULT_CAREGIVER_USAGE_PRIORITY,
    DEFAULT_DURATION_REDUCTION_PRIORITY,
    DEFAULT_SOFT_WINDOW_VIOLATION_LEVEL,
    DURATION_MINIMUM,
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

_GENDER_TO_HHS = {
    "MALE": "male",
    "FEMALE": "female",
    "OTHER": "other",
    "PREFER_NOT_TO_SAY": "prefer_not_to_say",
}

_TRAVEL_TO_HHS = {
    "DRIVING": "driving",
    "WALKING": "walking",
    "CYCLING": "bicycling",
}


def _map_gender(gender: str | None) -> str:
    if not gender:
        return "male"
    return _GENDER_TO_HHS.get(gender, gender.lower() if isinstance(gender, str) else "male")


def _map_travel(method: str | None) -> str:
    if not method:
        return "driving"
    return _TRAVEL_TO_HHS.get(method, "driving")


def _extend_feasibility(extend: bool) -> dict[str, Any]:
    return {
        "extend": bool(extend),
        "max_distance_km": 15.0 if extend else 5.0,
        "max_time_minutes": 45 if extend else 20,
        "max_distance_border_crossings_km": 10,
        "max_time_border_crossings_minutes": 60,
    }


def aggregate_request_window_priority(
    prid: int,
    caregiver_specs: list[dict[str, Any]],
) -> float:
    """Any coordinator must/only ⇒ 1.0; else any historical ⇒ 0.01; else 1.0."""
    key = str(prid)
    saw_historical = False
    for spec in caregiver_specs:
        only = set(spec.get("only_set") or [])
        only_sources = spec.get("only_set_sources") or {}
        must_sources = spec.get("must_visit_sources") or {}
        must = spec.get("must_visit_patients") or {}
        if prid in only or key in only_sources:
            src = only_sources.get(key, MUST_SOURCE_COORDINATOR)
            if src == MUST_SOURCE_COORDINATOR:
                return REQUEST_WINDOW_PRIORITY_COORDINATOR
            saw_historical = True
        if key in must or key in must_sources:
            src = must_sources.get(key, MUST_SOURCE_COORDINATOR)
            if src == MUST_SOURCE_COORDINATOR:
                return REQUEST_WINDOW_PRIORITY_COORDINATOR
            if src == MUST_SOURCE_HISTORICAL:
                saw_historical = True
    if saw_historical:
        return REQUEST_WINDOW_PRIORITY_HISTORICAL
    return REQUEST_WINDOW_PRIORITY_COORDINATOR


def fold_must_only_into_feasible(
    feasible: list[dict[str, Any]],
    caregiver_specs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply must=1.0 / only=2.0 / only-caregiver other pairs=0.0 onto feasible rows."""
    by_pair: dict[tuple[int, int], float] = {}
    for row in feasible:
        by_pair[(int(row["crid"]), int(row["prid"]))] = float(row["weight"])

    all_prids = {prid for _, prid in by_pair}
    for spec in caregiver_specs:
        crid = int(spec["crid"])
        only = [int(p) for p in (spec.get("only_set") or [])]
        must = {int(k): float(v) for k, v in (spec.get("must_visit_patients") or {}).items()}
        if only:
            only_set = set(only)
            for prid in all_prids:
                key = (crid, prid)
                if prid in only_set:
                    by_pair[key] = ROSTER_ONLY_VISIT_WEIGHT
                else:
                    by_pair[key] = ROSTER_HARD_EXCLUDE_WEIGHT
            for prid in only:
                by_pair[(crid, prid)] = ROSTER_ONLY_VISIT_WEIGHT
        else:
            for prid, _weight in must.items():
                by_pair[(crid, prid)] = ROSTER_MUST_VISIT_WEIGHT

    return [
        {"crid": crid, "prid": prid, "weight": weight}
        for (crid, prid), weight in sorted(by_pair.items())
    ]


def _soft_hard_windows(patient: dict[str, Any]) -> tuple[int, int, int, int]:
    matched = bool(patient.get("match_request"))
    margin = (
        MIN_MATCHED_PATIENT_WINDOW_SLACK_MINUTES
        if matched
        else MIN_PATIENT_WINDOW_SLACK_MINUTES
    )
    if patient.get("_soft_start") is not None and patient.get("_soft_end") is not None:
        soft_start = int(patient["_soft_start"])
        soft_end = int(patient["_soft_end"])
    elif patient.get("_pinned"):
        soft_start = int(patient["start_time"]) + margin
        soft_end = int(patient["end_time"]) - margin
    else:
        soft_start = int(patient.get("requested_start_time", patient["start_time"]))
        soft_end = int(patient.get("requested_end_time", patient["end_time"]))

    duration = int(patient["duration"])
    if soft_end - soft_start < duration:
        soft_end = soft_start + duration

    hard_start = min(int(patient["start_time"]), soft_start - margin)
    hard_end = max(int(patient["end_time"]), soft_end + margin)
    if soft_start - hard_start < margin:
        hard_start = soft_start - margin
    if hard_end - soft_end < margin:
        hard_end = soft_end + margin
    return hard_start, hard_end, soft_start, soft_end


def _normalize_min_duration(duration: int, raw_min: int | None) -> int:
    min_duration = int(raw_min) if raw_min is not None else max(DURATION_MINIMUM, duration - 10)
    if duration - min_duration < 10:
        min_duration = max(DURATION_MINIMUM, duration - 10)
    if min_duration > duration:
        min_duration = max(DURATION_MINIMUM, duration - 10)
    return min_duration


def to_wire_patient(
    patient: dict[str, Any],
    *,
    request_window_priority: float,
) -> dict[str, Any]:
    hard_start, hard_end, soft_start, soft_end = _soft_hard_windows(patient)
    duration = int(patient["duration"])
    min_duration = _normalize_min_duration(duration, patient.get("min_duration"))
    match_list = [str(p) for p in (patient.get("match_request") or [])]
    pid = str(patient["pid"])
    return {
        "pid": pid,
        "prid": str(patient["prid"]),
        "gender": _map_gender(patient.get("gender")),
        "location_id": pid,
        "location": {
            "latitude": float(patient["latitude"]),
            "longitude": float(patient["longitude"]),
            "postcode": None,
        },
        "request_window": {
            "start_time_hard": hard_start,
            "end_time_hard": hard_end,
            "start_time_soft": soft_start,
            "end_time_soft": soft_end,
            "duration": duration,
            "min_duration": min_duration,
            "duration_reduction_priority": DEFAULT_DURATION_REDUCTION_PRIORITY,
            "request_window_priority": float(request_window_priority),
            "soft_window_violation_level": DEFAULT_SOFT_WINDOW_VIOLATION_LEVEL,
            "match_request_list": match_list,
        },
        "extend_feasibility": _extend_feasibility(
            bool(patient.get("do_extend_feasiblity"))
        ),
    }


def to_wire_caregiver(entry: dict[str, Any]) -> dict[str, Any]:
    cid = str(entry["cid"])
    return {
        "cid": cid,
        "crid": str(entry["crid"]),
        "gender": _map_gender(entry.get("gender")),
        "travel_mode": _map_travel(entry.get("travel_method")),
        "location_id": cid,
        "location": {
            "latitude": float(entry["latitude"]),
            "longitude": float(entry["longitude"]),
            "postcode": None,
        },
        "current_location_id": cid,
        "start_location_id": cid,
        "end_location_id": cid,
        "shift": {
            "start_time": int(entry["start_time"]),
            "end_time": int(entry["end_time"]),
        },
        "extend_feasibility": _extend_feasibility(
            bool(entry.get("do_extend_feasiblity"))
        ),
        "caregiver_usage_priority": DEFAULT_CAREGIVER_USAGE_PRIORITY,
    }


def to_wire_feasible(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "crid": str(row["crid"]),
            "prid": str(row["prid"]),
            "weight": float(row["weight"]),
        }
        for row in rows
    ]
