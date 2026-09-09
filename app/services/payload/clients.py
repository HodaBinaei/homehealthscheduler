from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.services.payload.constants import (
    CLIENT_EXPORT_END_OF_DAY_EXTEND_THRESHOLD,
    GENDER_MAP,
    MIN_DURATION_FLOOR_RATIO,
    MIN_PATIENT_WINDOW_SLACK_MINUTES,
    MINUTES_IN_DAY,
    PATIENT_SEQUENCE_GAP_MINUTES,
)
from app.services.payload import queries
from app.services.payload.schedule_rules import (
    find_schedule_occurrence_covering_date,
    resolve_schedule_flexibility_window,
    resolve_schedule_visit_window,
)
from app.services.payload.time_utils import (
    add_days,
    day_name_from_date,
    normalize_engine_time_window,
)
from app.services.payload.window_helpers import (
    build_match_request_components,
    floor_min_duration,
    resolve_engine_patient_window_duration,
    sequence_client_visit_blocks,
    widen_match_group_window,
    widen_patient_window,
)

logger = logging.getLogger("hhs.payload.clients")


def _map_gender(gender: str | None) -> str:
    if not gender:
        return "MALE"
    return GENDER_MAP.get(gender, "MALE")


def collect_client_export_records(db: Session, target: date) -> dict[str, Any]:
    clients = queries.load_active_clients(db)
    client_ids = [int(c["id"]) for c in clients]
    schedules = queries.load_client_schedules(db, client_ids)
    cancelled = queries.load_cancelled_slot_keys(db, target)

    applicable: list[dict[str, Any]] = []
    for sched in schedules:
        if find_schedule_occurrence_covering_date(sched, target) is not None:
            applicable.append(sched)

    suppressed_sources: set[int] = set()
    for sched in applicable:
        prefs = sched.get("preferences") or {}
        source_id = prefs.get("source_schedule_id")
        if prefs.get("is_temporary") and source_id is not None:
            suppressed_sources.add(int(source_id))
    export_schedules = [s for s in applicable if s["id"] not in suppressed_sources]

    by_client: dict[int, list[dict[str, Any]]] = {}
    for sched in export_schedules:
        by_client.setdefault(int(sched["client_id"]), []).append(sched)

    client_map = {int(c["id"]): c for c in clients}
    entries: list[dict[str, Any]] = []
    incremental_key = 1
    prid = 1

    for client_id, client_schedules in by_client.items():
        client = client_map.get(client_id)
        if not client:
            continue
        for sched in client_schedules:
            flex = resolve_schedule_flexibility_window(sched, target)
            if not flex:
                continue
            effective_start, effective_end = normalize_engine_time_window(flex[0], flex[1])
            if effective_end <= effective_start:
                continue

            requested = resolve_schedule_visit_window(sched, target)
            if not requested:
                continue
            req_start, req_end = normalize_engine_time_window(requested[0], requested[1])

            segment_duration = effective_end - effective_start
            schedule_requested = sched.get("requested_duration")
            requested_duration = (
                int(schedule_requested) if schedule_requested is not None else segment_duration
            )
            duration = min(requested_duration, segment_duration)
            prefs = sched.get("preferences") or {}
            raw_min = prefs.get("min_duration")
            min_duration = floor_min_duration(
                duration,
                int(raw_min) if raw_min is not None else None,
                MIN_DURATION_FLOOR_RATIO,
            )

            availability_id = (
                int(prefs["source_schedule_id"])
                if prefs.get("is_temporary") and prefs.get("source_schedule_id") is not None
                else int(sched["id"])
            )
            if f"{availability_id}:0" in cancelled:
                continue

            entry = {
                "pid": client_id,
                "prid": prid,
                "first_name": client["name"],
                "last_name": client.get("lastname") or "",
                "start_time": effective_start,
                "end_time": effective_end,
                "requested_start_time": req_start,
                "requested_end_time": req_end,
                "requested_duration": requested_duration,
                "duration": duration,
                "min_duration": min_duration,
                "gender": _map_gender(client.get("gender")),
                "match_request": [],
                "fix_window": 0,
                "latitude": client.get("latitude"),
                "longitude": client.get("longitude"),
                "history_start": effective_start,
                "history_end": effective_end,
                "extendedFeasibility": bool(client.get("extended_feasibility")),
                "do_extend_feasiblity": bool(client.get("extended_feasibility")),
            }
            entries.append(
                {
                    "key": str(incremental_key),
                    "entry": entry,
                    "availability_id": availability_id,
                    "slot_index": 0,
                    "requested_start_time": req_start,
                    "requested_end_time": req_end,
                    "requested_duration": requested_duration,
                    "segment_group": None,
                    "schedule": sched,
                }
            )
            incremental_key += 1
            prid += 1

    return {
        "entries": entries,
        "schedules": schedules,
        "target_date": target,
        "day_of_week": day_name_from_date(target),
    }


def build_clients_output(db: Session, target: date) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    bundle = collect_client_export_records(db, target)
    entries: list[dict[str, Any]] = bundle["entries"]
    schedules: list[dict[str, Any]] = bundle["schedules"]

    # Overlap match_request within same pid
    by_pid: dict[int, list[dict[str, Any]]] = {}
    for rec in entries:
        by_pid.setdefault(rec["entry"]["pid"], []).append(rec)

    for pid_entries in by_pid.values():
        for i in range(len(pid_entries)):
            for j in range(i + 1, len(pid_entries)):
                a = pid_entries[i]
                b = pid_entries[j]
                if (
                    a["requested_start_time"] < b["requested_end_time"]
                    and b["requested_start_time"] < a["requested_end_time"]
                ):
                    a["entry"]["match_request"].append(b["entry"]["prid"])
                    b["entry"]["match_request"].append(a["entry"]["prid"])

    match_components = build_match_request_components(
        [{"prid": r["entry"]["prid"], "match_request": r["entry"]["match_request"]} for r in entries]
    )

    def apply_match_group_unify() -> None:
        entry_by_prid = {r["entry"]["prid"]: r["entry"] for r in entries}
        for component in match_components:
            members = [entry_by_prid[p] for p in component if p in entry_by_prid]
            if not members:
                continue
            start, end = widen_match_group_window(
                [
                    {
                        "prid": m["prid"],
                        "duration": m["duration"],
                        "requested_start_time": m["requested_start_time"],
                        "requested_end_time": m["requested_end_time"],
                        "history_start": m["history_start"],
                        "history_end": m["history_end"],
                    }
                    for m in members
                ]
            )
            for m in members:
                m["start_time"] = start
                m["end_time"] = end

    def apply_client_sequencing(max_end_by_pid: dict[int, int]) -> None:
        for pid, pid_records in by_pid.items():
            visits = [
                {
                    "prid": r["entry"]["prid"],
                    "start_time": r["entry"]["start_time"],
                    "end_time": r["entry"]["end_time"],
                    "duration": r["entry"]["duration"],
                    "requested_start_time": r["entry"]["requested_start_time"],
                    "requested_end_time": r["entry"]["requested_end_time"],
                }
                for r in pid_records
            ]
            try:
                sequenced = sequence_client_visit_blocks(
                    visits,
                    match_components,
                    gap_minutes=PATIENT_SEQUENCE_GAP_MINUTES,
                    min_slack=MIN_PATIENT_WINDOW_SLACK_MINUTES,
                    min_start_time=0,
                    max_end_time=max_end_by_pid.get(pid),
                )
                for r in pid_records:
                    window = sequenced.get(r["entry"]["prid"])
                    if window:
                        r["entry"]["start_time"] = window[0]
                        r["entry"]["end_time"] = window[1]
            except ValueError as exc:
                logger.warning(
                    "Skipping sequence for pid=%s: %s",
                    pid,
                    exc,
                )

    # 1 seed from history
    for r in entries:
        r["entry"]["start_time"] = r["entry"]["history_start"]
        r["entry"]["end_time"] = r["entry"]["history_end"]

    # 2 widen
    for r in entries:
        e = r["entry"]
        start, end = widen_patient_window(
            e["start_time"],
            e["end_time"],
            e["requested_start_time"],
            e["requested_end_time"],
            e["duration"],
        )
        e["start_time"] = start
        e["end_time"] = end

    # 3 unify
    apply_match_group_unify()

    # 4 provisional sequence
    provisional = {pid: MINUTES_IN_DAY * 2 for pid in by_pid}
    apply_client_sequencing(provisional)

    # 5 overnight extension
    next_date = add_days(target, 1)
    next_applicable = [
        s for s in schedules if find_schedule_occurrence_covering_date(s, next_date) is not None
    ]
    by_client_next: dict[int, list[dict[str, Any]]] = {}
    for s in next_applicable:
        by_client_next.setdefault(int(s["client_id"]), []).append(s)

    needing = {r["entry"]["pid"] for r in entries if r["entry"]["end_time"] >= CLIENT_EXPORT_END_OF_DAY_EXTEND_THRESHOLD}
    extension_by_client: dict[int, int] = {}
    for cid in needing:
        ext = 0
        for sched in by_client_next.get(cid, []):
            win = resolve_schedule_visit_window(sched, next_date)
            if win and win[0] == 0:
                ext = max(ext, win[1])
        extension_by_client[cid] = ext

    for r in entries:
        e = r["entry"]
        if e["end_time"] < CLIENT_EXPORT_END_OF_DAY_EXTEND_THRESHOLD:
            continue
        ext = extension_by_client.get(e["pid"], 0)
        if ext <= 0:
            continue
        e["end_time"] = MINUTES_IN_DAY + ext
        if e["requested_end_time"] >= CLIENT_EXPORT_END_OF_DAY_EXTEND_THRESHOLD:
            e["requested_end_time"] = MINUTES_IN_DAY + ext
        if e["history_end"] >= CLIENT_EXPORT_END_OF_DAY_EXTEND_THRESHOLD:
            e["history_end"] = MINUTES_IN_DAY + ext

    day_max_by_pid: dict[int, int] = {}
    for pid in by_pid:
        ext = extension_by_client.get(pid, 0)
        day_max_by_pid[pid] = (
            MINUTES_IN_DAY + ext if ext > 0 else CLIENT_EXPORT_END_OF_DAY_EXTEND_THRESHOLD
        )

    day_max_by_pid: dict[int, int] = {}
    for pid in by_pid:
        ext = extension_by_client.get(pid, 0)
        day_max_by_pid[pid] = (
            MINUTES_IN_DAY + ext if ext > 0 else CLIENT_EXPORT_END_OF_DAY_EXTEND_THRESHOLD
        )

    for r in entries:
        e = r["entry"]
        day_max = day_max_by_pid[e["pid"]]
        min_span = e["duration"] + MIN_PATIENT_WINDOW_SLACK_MINUTES
        span = max(e["end_time"] - e["start_time"], min_span)

        if e["start_time"] < 0 or e["end_time"] > day_max or span > day_max:
            if min_span > day_max:
                # Duration+slack cannot fit the day bound; use the full day window
                # and shrink duration so later sequencing can still place it.
                logger.warning(
                    "Patient prid=%s duration+slack=%s exceeds day_max=%s; clamping to full day",
                    e["prid"],
                    min_span,
                    day_max,
                )
                e["start_time"] = 0
                e["end_time"] = day_max
                e["duration"] = min(
                    e["duration"],
                    max(1, day_max - MIN_PATIENT_WINDOW_SLACK_MINUTES),
                )
            else:
                start = max(0, min(e["start_time"], day_max - span))
                e["start_time"] = start
                e["end_time"] = start + span
                if e["end_time"] > day_max:
                    e["end_time"] = day_max
                    e["start_time"] = max(0, day_max - min_span)
        elif e["end_time"] - e["start_time"] < min_span:
            start = max(0, min(e["start_time"], day_max - min_span))
            e["start_time"] = start
            e["end_time"] = start + min_span

    # 6–7 duration align + floor
    for r in entries:
        e = r["entry"]
        ws, we, dur = resolve_engine_patient_window_duration(
            e["start_time"],
            e["end_time"],
            e["requested_start_time"],
            e["requested_end_time"],
            r["requested_duration"],
        )
        e["start_time"] = ws
        e["end_time"] = we
        e["duration"] = dur
        e["min_duration"] = floor_min_duration(dur, e["min_duration"])

    # 8 re-unify + re-sequence
    apply_match_group_unify()
    apply_client_sequencing(day_max_by_pid)

    clients_output: dict[str, dict[str, Any]] = {}
    for r in entries:
        e = r["entry"]
        start, end = normalize_engine_time_window(e["start_time"], e["end_time"])
        match = e["match_request"] if e["match_request"] else None
        clients_output[r["key"]] = {
            **e,
            "start_time": start,
            "end_time": end,
            "match_request": match,
        }

    return clients_output, bundle
