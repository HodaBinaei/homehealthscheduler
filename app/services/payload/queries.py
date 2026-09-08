from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_time_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return str(value)[:5]


def _as_day_list(value: Any) -> list[str]:
    """Parse PostgreSQL enum/text arrays that may arrive as '{Monday,Tuesday}' strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        # list('{Monday}') wrongly yields character tokens — rejoin and re-parse.
        if items and all(isinstance(x, str) and len(x) <= 1 for x in items):
            return _as_day_list("".join(items))
        return [str(x).strip().strip('"') for x in items if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    if not text:
        return []
    return [part.strip().strip('"') for part in text.split(",") if part.strip()]


def _exec(db: Session, sql: str, params: dict[str, Any] | None = None, expanding: list[str] | None = None):
    stmt = text(sql)
    if expanding:
        stmt = stmt.bindparams(*[bindparam(name, expanding=True) for name in expanding])
    return db.execute(stmt, params or {})


def load_active_clients(db: Session) -> list[dict[str, Any]]:
    rows = _exec(
        db,
        """
        SELECT id, name, lastname, gender, latitude, longitude,
               extended_feasibility, status, not_send_to_engine
        FROM client
        WHERE status = 'Active' AND not_send_to_engine = false
        ORDER BY id
        """,
    ).mappings().all()
    return [dict(r) for r in rows]


def load_client_schedules(db: Session, client_ids: list[int]) -> list[dict[str, Any]]:
    if not client_ids:
        return []
    rows = _exec(
        db,
        """
        SELECT cs.id, cs.client_id, cs.days, cs.requested_start_time, cs.requested_end_time,
               cs.end_time_date_offset_days, cs.requested_duration, cs.start_date, cs.end_date,
               cs.occurs_every, cs.deleted_at,
               csp.id AS pref_id, csp.window_start, csp.window_end, csp.min_duration,
               csp.not_send_to_engine AS pref_not_send,
               csp.is_temporary, csp.is_unavailability, csp.effective_date_from,
               csp.effective_date_to, csp.source_schedule_id
        FROM client_schedules cs
        LEFT JOIN client_schedule_preferences csp ON csp.client_schedule_id = cs.id
        WHERE cs.client_id IN :ids
          AND cs.deleted_at IS NULL
          AND (csp.id IS NULL OR csp.not_send_to_engine = false)
        ORDER BY cs.client_id, cs.id
        """,
        {"ids": client_ids},
        expanding=["ids"],
    ).mappings().all()

    schedule_ids = [int(r["id"]) for r in rows]
    exceptions_by_schedule: dict[int, list[date]] = {sid: [] for sid in schedule_ids}
    if schedule_ids:
        ex_rows = _exec(
            db,
            """
            SELECT client_schedule_id, exception_date
            FROM client_schedule_exceptions
            WHERE client_schedule_id IN :ids
            """,
            {"ids": schedule_ids},
            expanding=["ids"],
        ).mappings().all()
        for ex in ex_rows:
            sid = int(ex["client_schedule_id"])
            d = _as_date(ex["exception_date"])
            if d:
                exceptions_by_schedule.setdefault(sid, []).append(d)

    only_by_pref: dict[int, list[int]] = {}
    must_by_pref: dict[int, list[int]] = {}
    dislike_by_pref: dict[int, list[int]] = {}
    pref_ids = [int(r["pref_id"]) for r in rows if r["pref_id"] is not None]
    if pref_ids:
        for table, bucket in (
            ("client_schedule_preferences_only_users", only_by_pref),
            ("client_schedule_preferences_must_users", must_by_pref),
        ):
            link_rows = _exec(
                db,
                f"""
                SELECT preferences_id, user_availability_id
                FROM {table}
                WHERE preferences_id IN :ids AND disabled = false
                """,
                {"ids": pref_ids},
                expanding=["ids"],
            ).mappings().all()
            for link in link_rows:
                bucket.setdefault(int(link["preferences_id"]), []).append(
                    int(link["user_availability_id"])
                )
        dislike_rows = _exec(
            db,
            """
            SELECT preferences_id, user_id
            FROM client_schedule_preferences_disliked_users
            WHERE preferences_id IN :ids AND disabled = false
            """,
            {"ids": pref_ids},
            expanding=["ids"],
        ).mappings().all()
        for link in dislike_rows:
            dislike_by_pref.setdefault(int(link["preferences_id"]), []).append(int(link["user_id"]))

    result: list[dict[str, Any]] = []
    for r in rows:
        prefs = None
        if r["pref_id"] is not None:
            pid = int(r["pref_id"])
            prefs = {
                "id": pid,
                "window_start": _as_time_str(r["window_start"]),
                "window_end": _as_time_str(r["window_end"]),
                "min_duration": r["min_duration"],
                "not_send_to_engine": r["pref_not_send"],
                "is_temporary": bool(r["is_temporary"]),
                "is_unavailability": bool(r["is_unavailability"]),
                "effective_date_from": _as_date(r["effective_date_from"]),
                "effective_date_to": _as_date(r["effective_date_to"]),
                "source_schedule_id": (
                    int(r["source_schedule_id"]) if r["source_schedule_id"] is not None else None
                ),
                "only_user_availability_ids": only_by_pref.get(pid, []),
                "must_user_availability_ids": must_by_pref.get(pid, []),
                "dislike_user_ids": dislike_by_pref.get(pid, []),
            }
        result.append(
            {
                "id": int(r["id"]),
                "client_id": int(r["client_id"]),
                "days": _as_day_list(r["days"]),
                "requested_start_time": _as_time_str(r["requested_start_time"]),
                "requested_end_time": _as_time_str(r["requested_end_time"]),
                "end_time_date_offset_days": int(r["end_time_date_offset_days"] or 0),
                "requested_duration": r["requested_duration"],
                "start_date": _as_date(r["start_date"]),
                "end_date": _as_date(r["end_date"]),
                "occurs_every": r["occurs_every"] or 1,
                "preferences": prefs,
                "exceptions": exceptions_by_schedule.get(int(r["id"]), []),
            }
        )
    return result


def load_active_users(db: Session) -> list[dict[str, Any]]:
    rows = _exec(
        db,
        """
        SELECT id, name, lastname, gender, travel_method, latitude, longitude,
               extended_feasibility, max_distance_km, max_p2p_distance_km, postcode
        FROM "user"
        WHERE status = 'Active' AND not_send_to_engine = false
        ORDER BY id
        """,
    ).mappings().all()
    return [dict(r) for r in rows]


def load_user_availabilities(db: Session, user_ids: list[int]) -> list[dict[str, Any]]:
    if not user_ids:
        return []
    rows = _exec(
        db,
        """
        SELECT ua.id, ua.user_id, ua.days, ua.start_time, ua.end_time,
               ua.end_time_date_offset_days, ua.start_date, ua.end_date, ua.occurs_every,
               ua.deleted_at,
               uap.id AS pref_id, uap.is_unavailability, uap.is_temporary,
               uap.effective_date_from, uap.effective_date_to, uap.not_send_to_engine
        FROM user_availabilities ua
        LEFT JOIN user_availability_preferences uap ON uap.user_availability_id = ua.id
        WHERE ua.user_id IN :ids
          AND ua.deleted_at IS NULL
          AND (uap.id IS NULL OR uap.is_unavailability = false)
        ORDER BY ua.user_id, ua.id
        """,
        {"ids": user_ids},
        expanding=["ids"],
    ).mappings().all()

    avail_ids = [int(r["id"]) for r in rows]
    exceptions_by_avail: dict[int, list[date]] = {aid: [] for aid in avail_ids}
    if avail_ids:
        ex_rows = _exec(
            db,
            """
            SELECT availability_id, exception_date
            FROM user_availability_exceptions
            WHERE availability_id IN :ids
            """,
            {"ids": avail_ids},
            expanding=["ids"],
        ).mappings().all()
        for ex in ex_rows:
            aid = int(ex["availability_id"])
            d = _as_date(ex["exception_date"])
            if d:
                exceptions_by_avail.setdefault(aid, []).append(d)

    only_by_pref: dict[int, list[int]] = {}
    must_by_pref: dict[int, list[int]] = {}
    dislike_by_pref: dict[int, list[int]] = {}
    pref_ids = [int(r["pref_id"]) for r in rows if r["pref_id"] is not None]
    if pref_ids:
        for table, bucket, col in (
            ("user_availability_preferences_only_clients", only_by_pref, "client_schedule_id"),
            ("user_availability_preferences_must_clients", must_by_pref, "client_schedule_id"),
        ):
            link_rows = _exec(
                db,
                f"""
                SELECT preferences_id, {col}
                FROM {table}
                WHERE preferences_id IN :ids AND disabled = false
                """,
                {"ids": pref_ids},
                expanding=["ids"],
            ).mappings().all()
            for link in link_rows:
                bucket.setdefault(int(link["preferences_id"]), []).append(int(link[col]))
        dislike_rows = _exec(
            db,
            """
            SELECT preferences_id, client_id
            FROM user_availability_preferences_disliked_clients
            WHERE preferences_id IN :ids AND disabled = false
            """,
            {"ids": pref_ids},
            expanding=["ids"],
        ).mappings().all()
        for link in dislike_rows:
            dislike_by_pref.setdefault(int(link["preferences_id"]), []).append(int(link["client_id"]))

    result: list[dict[str, Any]] = []
    for r in rows:
        prefs = None
        if r["pref_id"] is not None:
            pid = int(r["pref_id"])
            prefs = {
                "id": pid,
                "is_unavailability": bool(r["is_unavailability"]),
                "is_temporary": bool(r["is_temporary"]),
                "effective_date_from": _as_date(r["effective_date_from"]),
                "effective_date_to": _as_date(r["effective_date_to"]),
                "not_send_to_engine": bool(r["not_send_to_engine"]),
                "only_client_schedule_ids": only_by_pref.get(pid, []),
                "must_client_schedule_ids": must_by_pref.get(pid, []),
                "dislike_client_ids": dislike_by_pref.get(pid, []),
            }
        result.append(
            {
                "id": int(r["id"]),
                "user_id": int(r["user_id"]),
                "days": _as_day_list(r["days"]),
                "start_time": _as_time_str(r["start_time"]),
                "end_time": _as_time_str(r["end_time"]),
                "end_time_date_offset_days": int(r["end_time_date_offset_days"] or 0),
                "start_date": _as_date(r["start_date"]),
                "end_date": _as_date(r["end_date"]),
                "occurs_every": r["occurs_every"] or 1,
                "preferences": prefs,
                "exceptions": exceptions_by_avail.get(int(r["id"]), []),
            }
        )
    return result


def load_unavailability_slots(db: Session, user_ids: list[int]) -> list[dict[str, Any]]:
    if not user_ids:
        return []
    rows = _exec(
        db,
        """
        SELECT ua.id, ua.user_id, ua.days, ua.start_time, ua.end_time,
               ua.end_time_date_offset_days, ua.start_date, ua.end_date, ua.occurs_every,
               uap.is_temporary, uap.effective_date_from, uap.effective_date_to
        FROM user_availabilities ua
        JOIN user_availability_preferences uap ON uap.user_availability_id = ua.id
        WHERE ua.user_id IN :ids
          AND ua.deleted_at IS NULL
          AND uap.is_unavailability = true
        """,
        {"ids": user_ids},
        expanding=["ids"],
    ).mappings().all()

    avail_ids = [int(r["id"]) for r in rows]
    exceptions_by_avail: dict[int, list[date]] = {aid: [] for aid in avail_ids}
    if avail_ids:
        ex_rows = _exec(
            db,
            """
            SELECT availability_id, exception_date
            FROM user_availability_exceptions
            WHERE availability_id IN :ids
            """,
            {"ids": avail_ids},
            expanding=["ids"],
        ).mappings().all()
        for ex in ex_rows:
            aid = int(ex["availability_id"])
            d = _as_date(ex["exception_date"])
            if d:
                exceptions_by_avail.setdefault(aid, []).append(d)

    result: list[dict[str, Any]] = []
    for r in rows:
        result.append(
            {
                "id": int(r["id"]),
                "user_id": int(r["user_id"]),
                "days": _as_day_list(r["days"]),
                "start_time": _as_time_str(r["start_time"]),
                "end_time": _as_time_str(r["end_time"]),
                "end_time_date_offset_days": int(r["end_time_date_offset_days"] or 0),
                "start_date": _as_date(r["start_date"]),
                "end_date": _as_date(r["end_date"]),
                "occurs_every": r["occurs_every"] or 1,
                "preferences": {
                    "is_temporary": bool(r["is_temporary"]),
                    "is_unavailability": True,
                    "effective_date_from": _as_date(r["effective_date_from"]),
                    "effective_date_to": _as_date(r["effective_date_to"]),
                },
                "exceptions": exceptions_by_avail.get(int(r["id"]), []),
            }
        )
    return result


def load_approved_day_offs(db: Session, user_ids: list[int], target: date) -> list[dict[str, Any]]:
    if not user_ids:
        return []
    day_start = datetime.combine(target, time.min)
    day_end = datetime.combine(target, time.max)
    rows = _exec(
        db,
        """
        SELECT user_id, "startDateTime" AS start_dt, "endDateTime" AS end_dt
        FROM day_off_requests
        WHERE status = 'APPROVED'
          AND user_id IN :ids
          AND "startDateTime" <= :day_end
          AND COALESCE("endDateTime", "startDateTime") >= :day_start
        """,
        {"ids": user_ids, "day_start": day_start, "day_end": day_end},
        expanding=["ids"],
    ).mappings().all()
    return [dict(r) for r in rows]


def load_cancelled_slot_keys(db: Session, target: date) -> set[str]:
    rows = _exec(
        db,
        """
        SELECT rv.client_schedule_id, COALESCE(rv.slot_index, 0) AS slot_index
        FROM roster_visit rv
        JOIN roster r ON r.id = rv.roster_id
        WHERE r.date = :date
          AND rv.status = 'CANCELLED'
          AND rv.client_schedule_id IS NOT NULL
        """,
        {"date": target.isoformat()},
    ).mappings().all()
    return {f"{int(r['client_schedule_id'])}:{int(r['slot_index'])}" for r in rows}


def load_roster_visits(db: Session, target: date) -> list[dict[str, Any]]:
    rows = _exec(
        db,
        """
        SELECT rv.receiver_type, rv.receiver_client_id, rv.provider_user_id,
               rv.client_schedule_id, COALESCE(rv.slot_index, 0) AS slot_index,
               rv.start_minute, rv.end_minute, rv.status, rv.pinned
        FROM roster_visit rv
        JOIN roster r ON r.id = rv.roster_id
        WHERE r.date = :date
        """,
        {"date": target.isoformat()},
    ).mappings().all()
    return [dict(r) for r in rows]


def load_feasible_pairs(
    db: Session,
    caregiver_ids: list[int],
    client_ids: list[int],
    day_index: int,
) -> list[dict[str, Any]]:
    if not caregiver_ids or not client_ids:
        return []
    rows = _exec(
        db,
        """
        SELECT cgid, client_id, weight, day_of_week
        FROM feasible_pairs
        WHERE cgid IN :cgids
          AND client_id IN :client_ids
          AND (day_of_week = :day_index OR day_of_week IS NULL)
        """,
        {"cgids": caregiver_ids, "client_ids": client_ids, "day_index": day_index},
        expanding=["cgids", "client_ids"],
    ).mappings().all()
    return [dict(r) for r in rows]


def load_completed_distances(
    db: Session, method: str, entity_ids: list[int]
) -> list[dict[str, Any]]:
    if not entity_ids:
        return []
    rows = _exec(
        db,
        """
        SELECT from_id, to_id, distance_meters, duration_minutes
        FROM travel_distances
        WHERE travel_method = :method
          AND calculation_status = 'completed'
          AND from_id IN :ids
          AND to_id IN :ids
        """,
        {"method": method, "ids": entity_ids},
        expanding=["ids"],
    ).mappings().all()
    return [
        {
            "from_id": int(r["from_id"]),
            "to_id": int(r["to_id"]),
            "distance_meters": r["distance_meters"],
            "duration_minutes": r["duration_minutes"],
        }
        for r in rows
    ]


def load_user_must_clients(db: Session, user_ids: list[int]) -> dict[int, list[int]]:
    if not user_ids:
        return {}
    rows = _exec(
        db,
        """
        SELECT user_id, client_id
        FROM user_must_clients
        WHERE user_id IN :ids
        """,
        {"ids": user_ids},
        expanding=["ids"],
    ).mappings().all()
    result: dict[int, list[int]] = {}
    for r in rows:
        result.setdefault(int(r["user_id"]), []).append(int(r["client_id"]))
    return result


def load_client_preference_users(db: Session, client_ids: list[int]) -> dict[str, dict[int, list[int]]]:
    empty: dict[str, dict[int, list[int]]] = {"only": {}, "must": {}, "dislike": {}}
    if not client_ids:
        return empty

    for key, table, user_col in (
        ("only", "client_only_users", "user_id"),
        ("must", "client_must_users", "user_id"),
        ("dislike", "client_disliked_caregivers", "caregiver_id"),
    ):
        rows = _exec(
            db,
            f"""
            SELECT client_id, {user_col} AS user_id
            FROM {table}
            WHERE client_id IN :ids
            """,
            {"ids": client_ids},
            expanding=["ids"],
        ).mappings().all()
        for r in rows:
            empty[key].setdefault(int(r["client_id"]), []).append(int(r["user_id"]))
    return empty


def load_availability_user_map(db: Session, availability_ids: list[int]) -> dict[int, int]:
    if not availability_ids:
        return {}
    rows = _exec(
        db,
        """
        SELECT id, user_id FROM user_availabilities WHERE id IN :ids
        """,
        {"ids": availability_ids},
        expanding=["ids"],
    ).mappings().all()
    return {int(r["id"]): int(r["user_id"]) for r in rows}
