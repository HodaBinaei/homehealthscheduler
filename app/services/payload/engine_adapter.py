"""Adapt bridge wire payloads to engine-service scheduler request DTOs."""

from __future__ import annotations

from typing import Any

from app.services.payload.hhs_sanitize import sanitize_engine_request
from app.services.payload.travel_bounds import (
    clamp_distance_km,
    clamp_travel_minutes,
    coerce_travel_minutes,
    scrub_engine_distance_matrices,
)


def reshape_distance_data(
    matrix: dict[str, dict[str, float | None]] | None,
) -> dict[str, Any]:
    """Convert bridge {distance, duration} maps into engine DistanceDataDTO shape."""
    if not matrix:
        return {"distances": {}}

    distance = matrix.get("distance") or {}
    duration = matrix.get("duration") or {}
    distances: dict[str, dict[str, Any]] = {}

    for key, km in distance.items():
        if km is None:
            continue
        parts = str(key).split("_", 1)
        if len(parts) != 2:
            continue
        from_id, to_id = parts
        # Prefer same-key duration; fall back to str(key) for int/str key mismatches.
        minutes = duration.get(key)
        if minutes is None:
            minutes = duration.get(str(key))
        if coerce_travel_minutes(minutes) is None:
            continue
        distances[str(key)] = {
            "from_location_id": str(from_id),
            "to_location_id": str(to_id),
            "distance_km": clamp_distance_km(km, default=0.0),
            "distance_minute": clamp_travel_minutes(minutes, default=0),
        }

    return {"distances": distances}


def _common_people_and_distances(bundle: dict[str, Any]) -> dict[str, Any]:
    caregivers = bundle.get("caregivers") or {}
    patients = bundle.get("patients") or {}
    body = {
        "caregiver_dict": list(caregivers.values()),
        "patient_dict": list(patients.values()),
        "crid_prid_feasible_dict": list(bundle.get("crid_prid_feasible") or []),
        "walking_data": reshape_distance_data(bundle.get("walking_data")),
        "cycling_data": reshape_distance_data(bundle.get("cycling_data")),
        "driving_data": reshape_distance_data(bundle.get("driving_data")),
    }
    sanitized = sanitize_engine_request(body)
    # Final hard scrub — never rely on a single clamp path.
    return scrub_engine_distance_matrices(sanitized)


def build_last_schedule_from_roster(bundle: dict[str, Any]) -> dict[str, Any]:
    """Build a Schedule-shaped last_schedule_dict from allocated roster visits."""
    date_str = str(bundle.get("date") or "")
    caregivers: dict[str, dict[str, Any]] = bundle.get("caregivers") or {}
    patients: dict[str, dict[str, Any]] = bundle.get("patients") or {}
    roster = bundle.get("roster") or {}

    allocated: dict[int, list[int]] = roster.get("allocated_prids_by_caregiver_id") or {}
    visit_by_prid: dict[int, dict[str, Any]] = roster.get("visit_by_prid") or {}

    caregivers_by_cid = {int(c["cid"]): c for c in caregivers.values()}
    all_prids = {int(p["prid"]) for p in patients.values()}
    assigned_prids: set[int] = set()
    assigned_crids: list[str] = []
    caregiver_schedules: dict[str, dict[str, Any]] = {}

    for cid, prids in allocated.items():
        caregiver = caregivers_by_cid.get(int(cid))
        if not caregiver:
            continue
        crid = str(caregiver["crid"])
        visits: list[dict[str, Any]] = []
        for prid in prids:
            visit = visit_by_prid.get(int(prid))
            if not visit:
                continue
            start = int(visit["start_minute"])
            end = int(visit["end_minute"])
            visits.append(
                {
                    "prid": str(prid),
                    "start_time": start,
                    "end_time": end,
                    "duration": max(0, end - start),
                    "crid": crid,
                    "travel_time": 0,
                    "waiting_time": 0,
                }
            )
            assigned_prids.add(int(prid))
        if not visits:
            continue
        assigned_crids.append(crid)
        caregiver_schedules[crid] = {
            "cid": str(caregiver["cid"]),
            "crid": crid,
            "shift": dict(caregiver.get("shift") or {}),
            "location": dict(caregiver.get("location") or {}),
            "visits": visits,
        }

    unassigned_prids = sorted(str(p) for p in (all_prids - assigned_prids))
    all_crids = {str(c["crid"]) for c in caregivers.values()}
    unassigned_crids = sorted(c for c in all_crids if c not in caregiver_schedules)

    return {
        "date": date_str,
        "assigned_crid_list": assigned_crids,
        "assigned_prid_list": sorted(str(p) for p in assigned_prids),
        "unassigned_prid_list": unassigned_prids,
        "unassigned_crid_list": unassigned_crids,
        "removed_prid_list": [],
        "removed_crid_list": [],
        "caregiver_schedules": caregiver_schedules,
    }


def to_full_assignment_request(bundle: dict[str, Any]) -> dict[str, Any]:
    body = _common_people_and_distances(bundle)
    body["data_day"] = str(bundle.get("date") or "")
    # Engine pipelines treat config as a mapping (`x in config`); never send null.
    body["config"] = {}
    body["save_result_schedule"] = False
    return body


def to_multicpsat_request(bundle: dict[str, Any]) -> dict[str, Any]:
    body = _common_people_and_distances(bundle)
    body["config"] = {}
    return body


def to_reschedule_request(
    bundle: dict[str, Any],
    *,
    last_schedule: dict[str, Any] | None = None,
    rescheduling_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = _common_people_and_distances(bundle)
    body["data_name"] = str(bundle.get("date") or "")
    body["last_schedule_dict"] = last_schedule if last_schedule is not None else build_last_schedule_from_roster(bundle)
    body["rescheduling_meta_dict"] = rescheduling_meta if rescheduling_meta is not None else {}
    body["config"] = {}
    body["save_result_schedule"] = False
    return body
