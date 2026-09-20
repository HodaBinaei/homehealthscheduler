from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.services.payload import queries
from app.services.payload.schedule_rules import (
    find_availability_occurrence,
    find_schedule_occurrence_covering_date,
)
from app.services.payload.travel_bounds import (
    MAX_DISTANCE_KM,
    MAX_TRAVEL_MINUTES,
    clamp_distance_km,
    clamp_travel_minutes,
    coerce_travel_minutes,
)

logger = logging.getLogger("hhs.distances")


def populate_distance_matrix(
    rows: list[dict[str, Any]], entity_ids: set[int]
) -> dict[str, dict[str, float | None]]:
    distance: dict[str, float | None] = {}
    duration: dict[str, float | None] = {}
    n = len(entity_ids)
    capped_durations = 0

    for entity_id in entity_ids:
        key = f"{entity_id}_{entity_id}"
        distance[key] = 0
        duration[key] = 0

    off_diagonal_filled = 0
    for td in rows:
        from_id = int(td["from_id"])
        to_id = int(td["to_id"])
        key1 = f"{from_id}_{to_id}"
        key2 = f"{to_id}_{from_id}"
        distance_km = (
            clamp_distance_km(round(td["distance_meters"] / 1000, 4), default=0.0)
            if td["distance_meters"] is not None
            else None
        )
        # Cap at load: DB often has 1440 (24h) for unreachable OSRM pairs.
        raw_minutes = coerce_travel_minutes(td["duration_minutes"])
        if raw_minutes is None:
            duration_minutes: float | None = None
        else:
            if raw_minutes > MAX_TRAVEL_MINUTES:
                capped_durations += 1
            duration_minutes = float(clamp_travel_minutes(raw_minutes, default=0))
        if from_id != to_id:
            if key1 not in distance:
                off_diagonal_filled += 1
            if key2 != key1 and key2 not in distance:
                off_diagonal_filled += 1
        distance[key1] = distance_km
        distance[key2] = distance_km
        duration[key1] = duration_minutes
        duration[key2] = duration_minutes
    if capped_durations:
        logger.warning(
            "Capped %s travel_distances rows to max_minute=%s (max_km=%s)",
            capped_durations,
            MAX_TRAVEL_MINUTES,
            MAX_DISTANCE_KM,
        )

    expected = n * (n - 1)
    if off_diagonal_filled != expected:
        all_ids = list(entity_ids)
        for id1 in all_ids:
            for id2 in all_ids:
                key = f"{id1}_{id2}"
                if key not in distance:
                    distance[key] = 0 if id1 == id2 else None
                    duration[key] = 0 if id1 == id2 else None

    return {"distance": distance, "duration": duration}


def resolve_distance_entity_ids(db: Session, target: date) -> set[int]:
    """Geocoded users/clients with applicable availability on the date."""
    ids: set[int] = set()

    clients = queries.load_active_clients(db)
    client_ids = [int(c["id"]) for c in clients]
    schedules = queries.load_client_schedules(db, client_ids)
    client_map = {int(c["id"]): c for c in clients}
    for sched in schedules:
        if find_schedule_occurrence_covering_date(sched, target) is None:
            continue
        client = client_map.get(int(sched["client_id"]))
        if not client:
            continue
        if client.get("latitude") is None or client.get("longitude") is None:
            continue
        ids.add(int(client["id"]))

    users = queries.load_active_users(db)
    user_ids = [int(u["id"]) for u in users]
    avails = queries.load_user_availabilities(db, user_ids)
    user_map = {int(u["id"]): u for u in users}
    for avail in avails:
        if find_availability_occurrence(avail, target) is None:
            continue
        user = user_map.get(int(avail["user_id"]))
        if not user:
            continue
        if user.get("latitude") is None or user.get("longitude") is None:
            continue
        ids.add(int(user["id"]))

    return ids


def build_distance_matrix_data(
    db: Session, target: date, method: str, entity_ids: set[int]
) -> dict[str, dict[str, float | None]]:
    rows = queries.load_completed_distances(db, method, list(entity_ids))
    return populate_distance_matrix(rows, entity_ids)


def assert_matrices_complete(
    matrices: dict[str, dict[str, dict[str, float | None]]],
) -> None:
    missing: list[str] = []
    for name, matrix in matrices.items():
        for key, value in matrix["distance"].items():
            if value is None:
                missing.append(f"{name}.distance[{key}]")
                if len(missing) >= 10:
                    break
        if len(missing) >= 10:
            break
    if missing:
        raise ValueError(
            "Distance matrices incomplete for execute payload. "
            f"Sample missing keys: {missing}"
        )


def load_distance_matrices_for_execute(
    db: Session, target: date
) -> dict[str, dict[str, dict[str, float | None]]]:
    entity_ids = resolve_distance_entity_ids(db, target)
    walking = build_distance_matrix_data(db, target, "walk", entity_ids)
    cycling = build_distance_matrix_data(db, target, "bike", entity_ids)
    driving = build_distance_matrix_data(db, target, "car", entity_ids)
    result = {
        "walking_data": walking,
        "cycling_data": cycling,
        "driving_data": driving,
    }
    assert_matrices_complete(result)
    return result
