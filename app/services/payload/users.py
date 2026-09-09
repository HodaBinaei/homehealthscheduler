from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services.payload import queries
from app.services.payload.constants import (
    GENDER_MAP,
    MIN_CAREGIVER_SEGMENT_MINUTES,
    MINUTES_IN_DAY,
    ROSTER_MUST_VISIT_WEIGHT,
    TRAVEL_METHOD_MAP,
)
from app.services.payload.schedule_rules import (
    find_availability_occurrence,
    resolve_availability_window,
)
from app.services.payload.time_utils import (
    normalize_engine_time_window,
    subtract_blocking,
)
from app.services.payload.window_helpers import (
    empty_caregiver_preference_sets,
    filter_must_by_availability,
    filter_prids_overlapping_availability,
    merge_caregiver_preference_sets,
    omit_must_when_only_set_present,
    resolve_only_set_long_call_rule,
    unique_sorted,
)


def _map_gender(gender: str | None) -> str:
    if not gender:
        return "MALE"
    return GENDER_MAP.get(gender, "MALE")


def _map_travel(method: str | None) -> str:
    if not method:
        return "DRIVING"
    return TRAVEL_METHOD_MAP.get(method, "DRIVING")


def _blocking_for_user(
    user_id: int,
    target: date,
    day_offs: list[dict[str, Any]],
    unavail_slots: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    blocking: list[tuple[int, int]] = []
    day_start = datetime.combine(target, time.min, tzinfo=timezone.utc)
    day_end = day_start.replace(hour=23, minute=59, second=59)

    for off in day_offs:
        if int(off["user_id"]) != user_id:
            continue
        start_dt = off["start_dt"]
        end_dt = off.get("end_dt") or off["start_dt"]
        if isinstance(start_dt, str):
            start_dt = datetime.fromisoformat(start_dt)
        if isinstance(end_dt, str):
            end_dt = datetime.fromisoformat(end_dt)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        start_m = max(0, int((start_dt - day_start).total_seconds() // 60))
        end_m = min(MINUTES_IN_DAY, int((end_dt - day_start).total_seconds() // 60) + 1)
        if end_m > start_m:
            blocking.append((start_m, end_m))

    for slot in unavail_slots:
        if int(slot["user_id"]) != user_id:
            continue
        if find_availability_occurrence(slot, target) is None:
            continue
        win = resolve_availability_window(slot, target)
        if win:
            blocking.append(win)
    return blocking


def build_users_output(
    db: Session,
    target: date,
    client_bundle: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, bool]]]:
    entries = client_bundle["entries"]
    prid_by_availability: dict[int, int] = {}
    prids_by_client: dict[int, list[int]] = {}
    prid_windows: dict[int, tuple[int, int]] = {}
    prid_durations: dict[int, int] = {}

    for rec in entries:
        e = rec["entry"]
        prid_by_availability[rec["availability_id"]] = e["prid"]
        prids_by_client.setdefault(e["pid"], []).append(e["prid"])
        prid_windows[e["prid"]] = (e["requested_start_time"], e["requested_end_time"])
        prid_durations[e["prid"]] = rec["requested_duration"]

    users = queries.load_active_users(db)
    user_ids = [int(u["id"]) for u in users]
    availabilities = queries.load_user_availabilities(db, user_ids)
    unavail = queries.load_unavailability_slots(db, user_ids)
    day_offs = queries.load_approved_day_offs(db, user_ids, target)
    user_must_clients = queries.load_user_must_clients(db, user_ids)
    client_prefs = queries.load_client_preference_users(
        db, list(prids_by_client.keys())
    )

    # Map schedule preference only/must availability ids → user ids
    avail_ids_needed: list[int] = []
    for sched in client_bundle.get("schedules") or []:
        prefs = sched.get("preferences") or {}
        avail_ids_needed.extend(prefs.get("only_user_availability_ids") or [])
        avail_ids_needed.extend(prefs.get("must_user_availability_ids") or [])
    avail_to_user = queries.load_availability_user_map(db, unique_sorted(avail_ids_needed))

    applicable_by_user: dict[int, list[dict[str, Any]]] = {}
    for avail in availabilities:
        if find_availability_occurrence(avail, target) is not None:
            applicable_by_user.setdefault(int(avail["user_id"]), []).append(avail)

    # Precompute segments per user for preference overlap
    segments_by_user: dict[int, list[tuple[int, int]]] = {}
    for user_id, slots in applicable_by_user.items():
        blocking = _blocking_for_user(user_id, target, day_offs, unavail)
        segs: list[tuple[int, int]] = []
        for slot in slots:
            clipped = resolve_availability_window(slot, target)
            if not clipped:
                continue
            start, end = normalize_engine_time_window(clipped[0], clipped[1])
            for seg in subtract_blocking(start, end, blocking):
                if seg[1] - seg[0] >= MIN_CAREGIVER_SEGMENT_MINUTES:
                    segs.append(seg)
        segments_by_user[user_id] = segs

    # Client-derived preference sets
    client_derived: dict[int, dict[str, Any]] = {}

    def ensure(uid: int) -> dict[str, Any]:
        if uid not in client_derived:
            client_derived[uid] = empty_caregiver_preference_sets()
        return client_derived[uid]

    # Profile: client only/must/dislike
    for client_id, user_list in client_prefs["only"].items():
        prids = prids_by_client.get(client_id, [])
        for uid in user_list:
            sets = ensure(uid)
            sets["only_set"] = unique_sorted(
                sets["only_set"]
                + filter_prids_overlapping_availability(
                    prids, prid_windows, segments_by_user.get(uid, [])
                )
            )
    for client_id, user_list in client_prefs["must"].items():
        prids = prids_by_client.get(client_id, [])
        for uid in user_list:
            sets = ensure(uid)
            for prid in filter_prids_overlapping_availability(
                prids, prid_windows, segments_by_user.get(uid, [])
            ):
                sets["must_visit_patients"][str(prid)] = ROSTER_MUST_VISIT_WEIGHT
    for client_id, user_list in client_prefs["dislike"].items():
        prids = prids_by_client.get(client_id, [])
        for uid in user_list:
            sets = ensure(uid)
            sets["dislike_set"] = unique_sorted(sets["dislike_set"] + prids)

    # Schedule-level links
    for sched in client_bundle.get("schedules") or []:
        prefs = sched.get("preferences") or {}
        if not prefs:
            continue
        schedule_id = int(sched["id"])
        # temps map under source id for PRID lookup
        lookup_id = (
            int(prefs["source_schedule_id"])
            if prefs.get("is_temporary") and prefs.get("source_schedule_id") is not None
            else schedule_id
        )
        schedule_prid = prid_by_availability.get(lookup_id)
        schedule_prids = [schedule_prid] if schedule_prid is not None else []
        client_prids = prids_by_client.get(int(sched["client_id"]), [])

        for avail_id in prefs.get("only_user_availability_ids") or []:
            uid = avail_to_user.get(int(avail_id))
            if uid is None:
                continue
            sets = ensure(uid)
            sets["only_set"] = unique_sorted(
                sets["only_set"]
                + filter_prids_overlapping_availability(
                    schedule_prids, prid_windows, segments_by_user.get(uid, [])
                )
            )
        for avail_id in prefs.get("must_user_availability_ids") or []:
            uid = avail_to_user.get(int(avail_id))
            if uid is None:
                continue
            sets = ensure(uid)
            for prid in filter_prids_overlapping_availability(
                schedule_prids, prid_windows, segments_by_user.get(uid, [])
            ):
                sets["must_visit_patients"][str(prid)] = ROSTER_MUST_VISIT_WEIGHT
        for uid in prefs.get("dislike_user_ids") or []:
            sets = ensure(int(uid))
            sets["dislike_set"] = unique_sorted(sets["dislike_set"] + client_prids)

    # User profile must clients
    for uid, client_ids in user_must_clients.items():
        sets = ensure(uid)
        prids: list[int] = []
        for cid in client_ids:
            prids.extend(prids_by_client.get(cid, []))
        for prid in filter_prids_overlapping_availability(
            prids, prid_windows, segments_by_user.get(uid, [])
        ):
            sets["must_visit_patients"][str(prid)] = ROSTER_MUST_VISIT_WEIGHT

    user_map = {int(u["id"]): u for u in users}
    users_output: dict[str, dict[str, Any]] = {}
    preference_flags: dict[int, dict[str, bool]] = {}
    incremental_key = 1
    crid = 1

    for user_id, user_avails in applicable_by_user.items():
        user = user_map.get(user_id)
        if not user:
            continue
        blocking = _blocking_for_user(user_id, target, day_offs, unavail)
        user_segments = segments_by_user.get(user_id, [])

        for avail in user_avails:
            clipped = resolve_availability_window(avail, target)
            if not clipped:
                continue
            start, end = normalize_engine_time_window(clipped[0], clipped[1])
            segments = subtract_blocking(start, end, blocking)

            prefs = avail.get("preferences") or {}
            flags = preference_flags.get(user_id) or {
                "hasOnlyLinks": False,
                "hasMustLinks": False,
            }
            only_schedule_ids = prefs.get("only_client_schedule_ids") or []
            must_schedule_ids = prefs.get("must_client_schedule_ids") or []
            if only_schedule_ids:
                flags["hasOnlyLinks"] = True
            if must_schedule_ids:
                flags["hasMustLinks"] = True
            preference_flags[user_id] = flags

            only_raw = [
                prid_by_availability[sid]
                for sid in only_schedule_ids
                if sid in prid_by_availability
            ]
            only_set = filter_prids_overlapping_availability(
                only_raw, prid_windows, user_segments
            )
            dislike_raw: list[int] = []
            for cid in prefs.get("dislike_client_ids") or []:
                dislike_raw.extend(prids_by_client.get(int(cid), []))
            dislike_set = unique_sorted(dislike_raw)

            must_raw: dict[str, float] = {}
            for sid in must_schedule_ids:
                if sid in prid_by_availability:
                    must_raw[str(prid_by_availability[sid])] = ROSTER_MUST_VISIT_WEIGHT
            must_visit = filter_must_by_availability(must_raw, user_segments, prid_windows)

            merged = merge_caregiver_preference_sets(
                {
                    "only_set": only_set,
                    "dislike_set": dislike_set,
                    "must_visit_patients": must_visit,
                },
                client_derived.get(user_id) or empty_caregiver_preference_sets(),
            )
            long_call_sets, demoted = resolve_only_set_long_call_rule(
                merged, prid_durations
            )
            resolved = omit_must_when_only_set_present(long_call_sets)
            if demoted:
                flags = preference_flags.get(user_id) or {
                    "hasOnlyLinks": False,
                    "hasMustLinks": False,
                }
                flags["hasOnlyLinks"] = False
                if resolved["must_visit_patients"]:
                    flags["hasMustLinks"] = True
                preference_flags[user_id] = flags
            elif resolved["only_set"]:
                flags = preference_flags.get(user_id) or {
                    "hasOnlyLinks": False,
                    "hasMustLinks": False,
                }
                flags["hasMustLinks"] = False
                preference_flags[user_id] = flags
            elif resolved["must_visit_patients"]:
                flags = preference_flags.get(user_id) or {
                    "hasOnlyLinks": False,
                    "hasMustLinks": False,
                }
                flags["hasMustLinks"] = True
                preference_flags[user_id] = flags

            for seg_start, seg_end in segments:
                if seg_end - seg_start < MIN_CAREGIVER_SEGMENT_MINUTES:
                    continue
                segment_must = filter_must_by_availability(
                    resolved["must_visit_patients"],
                    [(seg_start, seg_end)],
                    prid_windows,
                )
                users_output[str(incremental_key)] = {
                    "crid": crid,
                    "cid": user_id,
                    "first_name": user["name"],
                    "last_name": user.get("lastname") or "",
                    "start_time": seg_start,
                    "end_time": seg_end,
                    "gender": _map_gender(user.get("gender")),
                    "travel_method": _map_travel(user.get("travel_method")),
                    "only_set": [],  # computed only used for flags / long-call; execute clears
                    "dislike_set": resolved["dislike_set"],
                    "must_visit_patients": segment_must,
                    "latitude": user.get("latitude"),
                    "longitude": user.get("longitude"),
                    "extendedFeasibility": bool(user.get("extended_feasibility")),
                    "do_extend_feasiblity": bool(user.get("extended_feasibility")),
                    "_computed_only_set": resolved["only_set"],
                }
                incremental_key += 1
                crid += 1

    return users_output, preference_flags
