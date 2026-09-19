"""Filter a day bundle to a FE-selected subset of carers and visits."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _as_int_set(values: Iterable[Any]) -> set[int]:
    return {int(v) for v in values}


def _trim_matrix(
    matrix: dict[str, dict[str, float | None]] | None,
    location_ids: set[str],
) -> dict[str, dict[str, float | None]]:
    if not matrix:
        return {"distance": {}, "duration": {}}

    distance = matrix.get("distance") or {}
    duration = matrix.get("duration") or {}
    out_distance: dict[str, float | None] = {}
    out_duration: dict[str, float | None] = {}

    for key, km in distance.items():
        parts = str(key).split("_", 1)
        if len(parts) != 2:
            continue
        from_id, to_id = parts
        if from_id not in location_ids or to_id not in location_ids:
            continue
        out_distance[str(key)] = km
        if key in duration:
            out_duration[str(key)] = duration[key]

    return {"distance": out_distance, "duration": out_duration}


def resolve_prids_for_visit_ids(
    db: Session,
    target: date,
    visit_ids: list[str],
    *,
    prid_by_slot: dict[str, int],
) -> list[int]:
    """
    Map roster visit UUIDs on `target` to engine patient prids.

    Raises ValueError when visits are missing, on the wrong date, cancelled,
    ad-hoc (no schedule slot), or otherwise not in the engine patient set.
    """
    from app.services.payload import queries

    if not visit_ids:
        raise ValueError("visitIds must be a non-empty list")

    unique_ids = list(dict.fromkeys(str(v) for v in visit_ids))
    rows = queries.load_roster_visits_by_ids(db, target, unique_ids)
    found = {str(r["id"]): r for r in rows}

    missing = [vid for vid in unique_ids if vid not in found]
    if missing:
        raise ValueError(
            f"visitIds not found on date {target.isoformat()}: {', '.join(missing[:5])}"
            + ("…" if len(missing) > 5 else "")
        )

    prids: list[int] = []
    seen: set[int] = set()
    for vid in unique_ids:
        visit = found[vid]
        if visit.get("status") == "CANCELLED":
            raise ValueError(f"visitId {vid} is CANCELLED and cannot be scheduled")
        if visit.get("receiver_type") != "CLIENT" or visit.get("client_schedule_id") is None:
            raise ValueError(
                f"visitId {vid} has no client schedule slot (ad-hoc visits are not supported)"
            )
        slot_key = f"{int(visit['client_schedule_id'])}:{int(visit['slot_index'])}"
        prid = prid_by_slot.get(slot_key)
        if prid is None:
            raise ValueError(
                f"visitId {vid} has no engine patient (prid) for this date"
            )
        if prid not in seen:
            seen.add(prid)
            prids.append(int(prid))

    if not prids:
        raise ValueError("No patients matched the selected visitIds for this date")
    return prids


def filter_day_bundle_by_selection(
    bundle: dict[str, Any],
    *,
    provider_user_ids: list[int],
    prids: list[int],
) -> dict[str, Any]:
    """
    Keep caregivers whose cid ∈ providerUserIds and patients whose prid ∈ prids.

    Also trims feasible pairs and distance matrices to the remaining location ids.
    Raises ValueError when selection is empty or yields no carers/patients.
    """
    if not provider_user_ids:
        raise ValueError("providerUserIds must be a non-empty list")
    if not prids:
        raise ValueError("visitIds must resolve to at least one patient")

    user_set = _as_int_set(provider_user_ids)
    prid_set = {int(p) for p in prids}

    caregivers_in = bundle.get("caregivers") or {}
    patients_in = bundle.get("patients") or {}

    caregivers = {
        key: cg
        for key, cg in caregivers_in.items()
        if int(cg.get("cid")) in user_set
    }
    patients = {
        key: pt
        for key, pt in patients_in.items()
        if int(pt.get("prid")) in prid_set
    }

    if not caregivers:
        raise ValueError(
            "No caregivers matched the selected providerUserIds for this date"
        )
    if not patients:
        raise ValueError("No patients matched the selected visitIds for this date")

    crids = {str(cg["crid"]) for cg in caregivers.values()}
    kept_prids = {str(pt["prid"]) for pt in patients.values()}

    feasible = [
        row
        for row in (bundle.get("crid_prid_feasible") or [])
        if str(row.get("crid")) in crids and str(row.get("prid")) in kept_prids
    ]

    location_ids = {str(cg["cid"]) for cg in caregivers.values()} | {
        str(pt["pid"]) for pt in patients.values()
    }

    return {
        **bundle,
        "caregivers": caregivers,
        "patients": patients,
        "crid_prid_feasible": feasible,
        "walking_data": _trim_matrix(bundle.get("walking_data"), location_ids),
        "cycling_data": _trim_matrix(bundle.get("cycling_data"), location_ids),
        "driving_data": _trim_matrix(bundle.get("driving_data"), location_ids),
    }
