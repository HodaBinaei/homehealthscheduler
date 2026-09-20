from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.services.payload import queries
from app.services.payload.clients import build_clients_output
from app.services.payload.distances import load_distance_matrices_for_execute
from app.services.payload.feasible import build_crid_prid_feasible
from app.services.payload.time_utils import normalize_engine_time_window
from app.services.payload.users import build_users_output
from app.services.payload.window_helpers import (
    apply_roster_caregiver_specification,
    apply_roster_patient_specification,
)
from app.services.payload.wire_shape import (
    aggregate_request_window_priority,
    fold_must_only_into_feasible,
    to_wire_caregiver,
    to_wire_feasible,
    to_wire_patient,
)


def _load_roster_context(
    db: Session,
    target: date,
    client_bundle: dict[str, Any],
    preference_flags: dict[int, dict[str, bool]],
) -> dict[str, Any]:
    prid_by_slot: dict[str, int] = {}
    for rec in client_bundle["entries"]:
        slot = int(rec["slot_index"])
        prid = int(rec["entry"]["prid"])
        # Canonical / temp-source key used by Panel ingest.
        prid_by_slot[f"{rec['availability_id']}:{slot}"] = prid
        # Also key by the raw visit schedule id so multicpsat visit→prid lookup
        # works when the visit still points at a temporary amendment row.
        visit = rec.get("visit") or {}
        raw_schedule_id = visit.get("client_schedule_id")
        if raw_schedule_id is not None:
            prid_by_slot[f"{int(raw_schedule_id)}:{slot}"] = prid

    allocated: dict[int, list[int]] = {}
    pinned: dict[int, list[int]] = {}
    cancelled: set[int] = set()
    visit_by_prid: dict[int, dict[str, Any]] = {}

    for visit in queries.load_roster_visits(db, target):
        if visit.get("receiver_type") != "CLIENT" or visit.get("client_schedule_id") is None:
            continue
        slot_key = f"{int(visit['client_schedule_id'])}:{int(visit['slot_index'])}"
        prid = prid_by_slot.get(slot_key)
        if prid is None:
            continue
        status = visit.get("status")
        if status == "CANCELLED":
            cancelled.add(prid)
            continue
        if status == "ALLOCATED" and visit.get("provider_user_id") is not None:
            uid = int(visit["provider_user_id"])
            allocated.setdefault(uid, []).append(prid)
            if visit.get("pinned"):
                pinned.setdefault(uid, []).append(prid)
            visit_by_prid[prid] = {
                "start_minute": int(visit["start_minute"]),
                "end_minute": int(visit["end_minute"]),
                "pinned": bool(visit.get("pinned")),
            }

    return {
        "allocated_prids_by_caregiver_id": allocated,
        "pinned_prids_by_caregiver_id": pinned,
        "cancelled_prids": cancelled,
        "visit_by_prid": visit_by_prid,
        "caregiver_preferences_by_user_id": preference_flags,
        "prid_by_slot": prid_by_slot,
    }


def assemble_execute_records_for_date(
    db: Session, target: date
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    caregivers, patients, feasible, _roster = assemble_execute_records_with_roster(db, target)
    return caregivers, patients, feasible


def assemble_execute_records_with_roster(
    db: Session, target: date
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    clients_output, client_bundle = build_clients_output(db, target)
    users_output, preference_flags = build_users_output(db, target, client_bundle)

    ungeocoded_cids = sorted(
        {
            int(u["cid"])
            for u in users_output.values()
            if u.get("latitude") is None or u.get("longitude") is None
        }
    )
    ungeocoded_pids = sorted(
        {
            int(p["pid"])
            for p in clients_output.values()
            if p.get("latitude") is None or p.get("longitude") is None
        }
    )
    if ungeocoded_cids or ungeocoded_pids:
        raise ValueError(
            "Engine payload requires latitude/longitude on every caregiver and client. "
            f"ungeocodedCaregiverCids={ungeocoded_cids} ungeocodedClientPids={ungeocoded_pids}"
        )

    crid_prid_feasible = build_crid_prid_feasible(db, users_output, clients_output, target)
    roster = _load_roster_context(db, target, client_bundle, preference_flags)

    patient_sources: dict[int, dict[str, Any]] = {}
    for entry in clients_output.values():
        patient_sources[int(entry["prid"])] = {
            "requested_start_time": entry["requested_start_time"],
            "requested_end_time": entry["requested_end_time"],
            "history_start": entry["history_start"],
            "history_end": entry["history_end"],
            "min_duration": entry.get("min_duration"),
        }

    caregiver_specs: list[dict[str, Any]] = []
    caregivers: dict[str, dict[str, Any]] = {}
    for key, entry in users_output.items():
        start, end = normalize_engine_time_window(entry["start_time"], entry["end_time"])
        flags = preference_flags.get(int(entry["cid"])) or {
            "hasOnlyLinks": False,
            "hasMustLinks": False,
        }
        only_set, must, must_sources, only_sources = apply_roster_caregiver_specification(
            only_set=list(entry.get("_computed_only_set") or []),
            must_visit_patients=dict(entry.get("must_visit_patients") or {}),
            has_only_links=bool(flags.get("hasOnlyLinks")),
            pinned_prids=roster["pinned_prids_by_caregiver_id"].get(int(entry["cid"]), []),
            cancelled_prids=roster["cancelled_prids"],
            must_visit_sources=dict(entry.get("must_visit_sources") or {}),
            only_set_sources=dict(entry.get("_computed_only_set_sources") or {}),
        )
        caregiver_specs.append(
            {
                "crid": entry["crid"],
                "only_set": only_set,
                "must_visit_patients": must,
                "must_visit_sources": must_sources,
                "only_set_sources": only_sources,
            }
        )
        caregivers[key] = to_wire_caregiver(
            {
                **entry,
                "start_time": start,
                "end_time": end,
            }
        )

    patients_flat: dict[str, dict[str, Any]] = {}
    for key, entry in clients_output.items():
        start, end = normalize_engine_time_window(entry["start_time"], entry["end_time"])
        patient = {
            "pid": entry["pid"],
            "prid": entry["prid"],
            "first_name": entry["first_name"],
            "last_name": entry["last_name"],
            "start_time": start,
            "end_time": end,
            "duration": entry["duration"],
            "min_duration": entry.get("min_duration"),
            "requested_start_time": entry["requested_start_time"],
            "requested_end_time": entry["requested_end_time"],
            "requested_duration": entry["requested_duration"],
            "gender": entry["gender"],
            "match_request": entry.get("match_request"),
            "fix_window": entry.get("fix_window", 0),
            "latitude": entry["latitude"],
            "longitude": entry["longitude"],
            "do_extend_feasiblity": bool(entry.get("do_extend_feasiblity")),
        }
        source = patient_sources.get(int(entry["prid"]))
        roster_visit = roster["visit_by_prid"].get(int(entry["prid"]))
        matched = bool(entry.get("match_request"))
        if source:
            patient = apply_roster_patient_specification(
                patient, source, roster_visit, matched=matched
            )
        patients_flat[key] = patient

    # Drop cancelled patients from wire payload
    cancelled = roster["cancelled_prids"]
    patients_flat = {
        k: v for k, v in patients_flat.items() if int(v["prid"]) not in cancelled
    }

    crid_prid_feasible = fold_must_only_into_feasible(crid_prid_feasible, caregiver_specs)

    patients: dict[str, dict[str, Any]] = {}
    for key, patient in patients_flat.items():
        priority = aggregate_request_window_priority(int(patient["prid"]), caregiver_specs)
        patients[key] = to_wire_patient(patient, request_window_priority=priority)

    return caregivers, patients, to_wire_feasible(crid_prid_feasible), roster


def build_execute_payload_for_date(db: Session, target: date) -> dict[str, Any]:
    caregivers, patients, crid_prid_feasible = assemble_execute_records_for_date(db, target)
    matrices = load_distance_matrices_for_execute(db, target)
    return {
        "data": {
            "caregivers": caregivers,
            "patient": patients,
            "crid_prid_feasible": crid_prid_feasible,
            "walking_data": matrices["walking_data"],
            "cycling_data": matrices["cycling_data"],
            "driving_data": matrices["driving_data"],
            "date_data": target.isoformat(),
        }
    }


def build_day_bundle_for_engine(db: Session, target: date) -> dict[str, Any]:
    """Assemble wire records + matrices + roster context for engine-service adapters."""
    caregivers, patients, feasible, roster = assemble_execute_records_with_roster(db, target)
    matrices = load_distance_matrices_for_execute(db, target)
    return {
        "date": target.isoformat(),
        "caregivers": caregivers,
        "patients": patients,
        "crid_prid_feasible": feasible,
        "walking_data": matrices["walking_data"],
        "cycling_data": matrices["cycling_data"],
        "driving_data": matrices["driving_data"],
        "roster": roster,
    }
