from __future__ import annotations

from typing import Any

from app.services.payload.constants import (
    MIN_DURATION_FLOOR_RATIO,
    MIN_ONLY_SET_LONG_CALL_MINUTES,
    MIN_PATIENT_WINDOW_SLACK_MINUTES,
    PATIENT_SEQUENCE_GAP_MINUTES,
    ROSTER_MUST_VISIT_WEIGHT,
)


def unique_sorted(values: list[int]) -> list[int]:
    return sorted(set(values))


def resolve_only_set_long_call_rule(
    sets: dict[str, Any],
    prid_durations: dict[int, int],
    min_minutes: int = MIN_ONLY_SET_LONG_CALL_MINUTES,
) -> tuple[dict[str, Any], bool]:
    only_set = list(sets.get("only_set") or [])
    if len(only_set) <= 1:
        return sets, False

    long_prids = [p for p in only_set if prid_durations.get(p, 0) >= min_minutes]
    if long_prids:
        return {**sets, "only_set": unique_sorted(long_prids)}, False

    must = dict(sets.get("must_visit_patients") or {})
    for prid in only_set:
        must[str(prid)] = ROSTER_MUST_VISIT_WEIGHT
    return {**sets, "only_set": [], "must_visit_patients": must}, True


def omit_must_when_only_set_present(sets: dict[str, Any]) -> dict[str, Any]:
    if not sets.get("only_set"):
        return sets
    return {**sets, "must_visit_patients": {}}


def merge_caregiver_preference_sets(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    must = dict(a.get("must_visit_patients") or {})
    must.update(b.get("must_visit_patients") or {})
    return {
        "only_set": unique_sorted((a.get("only_set") or []) + (b.get("only_set") or [])),
        "dislike_set": unique_sorted((a.get("dislike_set") or []) + (b.get("dislike_set") or [])),
        "must_visit_patients": must,
    }


def empty_caregiver_preference_sets() -> dict[str, Any]:
    return {"only_set": [], "dislike_set": [], "must_visit_patients": {}}


def filter_prids_overlapping_availability(
    prids: list[int],
    windows: dict[int, tuple[int, int]],
    segments: list[tuple[int, int]],
) -> list[int]:
    result: list[int] = []
    for prid in prids:
        win = windows.get(prid)
        if not win:
            continue
        p_start, p_end = win
        for s, e in segments:
            if p_start < e and s < p_end:
                result.append(prid)
                break
    return unique_sorted(result)


def filter_must_by_availability(
    must: dict[str, float],
    segments: list[tuple[int, int]],
    windows: dict[int, tuple[int, int]],
) -> dict[str, float]:
    kept: dict[str, float] = {}
    for key, weight in must.items():
        prid = int(key)
        win = windows.get(prid)
        if not win:
            continue
        p_start, p_end = win
        for s, e in segments:
            if p_start < e and s < p_end:
                kept[key] = weight
                break
    return kept


def widen_patient_window(
    start_time: int,
    end_time: int,
    requested_start_time: int,
    requested_end_time: int,
    duration: int,
    min_slack: int = MIN_PATIENT_WINDOW_SLACK_MINUTES,
) -> tuple[int, int]:
    min_span = duration + min_slack
    if end_time - start_time >= min_span:
        return start_time, end_time
    center = (requested_start_time + requested_end_time) / 2
    start = round(center - min_span / 2)
    return start, start + min_span


def widen_match_group_window(
    members: list[dict[str, int]],
    min_slack: int = MIN_PATIENT_WINDOW_SLACK_MINUTES,
) -> tuple[int, int]:
    if not members:
        return 0, 0
    req_start = members[0]["requested_start_time"]
    req_end = members[0]["requested_end_time"]
    max_duration = members[0]["duration"]
    max_history_span = 0
    for m in members:
        req_start = min(req_start, m["requested_start_time"])
        req_end = max(req_end, m["requested_end_time"])
        max_duration = max(max_duration, m["duration"])
        max_history_span = max(
            max_history_span, max(0, m["history_end"] - m["history_start"])
        )
    span = max(req_end - req_start, max_history_span, max_duration + min_slack)
    center = (req_start + req_end) / 2
    start = round(center - span / 2)
    return start, start + span


def sequence_patient_windows(
    windows: list[dict[str, int]],
    *,
    gap_minutes: int = PATIENT_SEQUENCE_GAP_MINUTES,
    min_slack: int = MIN_PATIENT_WINDOW_SLACK_MINUTES,
    min_start_time: int | None = None,
    max_end_time: int | None = None,
) -> list[dict[str, int]]:
    if not windows:
        return []

    result = []
    for w in windows:
        min_span = w["duration"] + min_slack
        span = max(w["end_time"] - w["start_time"], min_span)
        result.append(
            {
                "start_time": w["start_time"],
                "end_time": w["start_time"] + span,
                "duration": w["duration"],
            }
        )

    if len(result) > 1:
        for k in range(len(result) - 1):
            min_next = result[k]["start_time"] + result[k]["duration"] + gap_minutes
            if result[k + 1]["start_time"] < min_next:
                span = result[k + 1]["end_time"] - result[k + 1]["start_time"]
                result[k + 1]["start_time"] = min_next
                result[k + 1]["end_time"] = min_next + span
        for k in range(len(result) - 1, 0, -1):
            max_prev = result[k]["start_time"] - result[k - 1]["duration"] - gap_minutes
            if result[k - 1]["start_time"] > max_prev:
                span = result[k - 1]["end_time"] - result[k - 1]["start_time"]
                result[k - 1]["start_time"] = max_prev
                result[k - 1]["end_time"] = max_prev + span

    if min_start_time is None and max_end_time is None:
        return result

    clamped: list[dict[str, int]] = []
    for idx, w in enumerate(result):
        min_span = w["duration"] + min_slack
        start = w["start_time"]
        end = w["end_time"]
        span = max(end - start, min_span)
        if min_start_time is not None and start < min_start_time:
            start = min_start_time
            end = start + span
        else:
            end = start + span
        if max_end_time is not None and end > max_end_time:
            end = max_end_time
            start = end - span
        if (
            (min_start_time is not None and start < min_start_time)
            or (max_end_time is not None and end > max_end_time)
            or end - start < min_span
        ):
            raise ValueError(
                f"sequencePatientWindows: window[{idx}] cannot fit duration={w['duration']} "
                f"+ slack={min_slack} within [{min_start_time}, {max_end_time}]"
            )
        clamped.append({"start_time": start, "end_time": end, "duration": w["duration"]})
    return clamped


def sequence_client_visit_blocks(
    visits: list[dict[str, int]],
    match_components: list[list[int]],
    **options: Any,
) -> dict[int, tuple[int, int]]:
    result: dict[int, tuple[int, int]] = {}
    if not visits:
        return result

    visit_by_prid = {v["prid"]: v for v in visits}
    assigned: set[int] = set()
    blocks: list[dict[str, Any]] = []

    for component in match_components:
        members = [visit_by_prid[p] for p in component if p in visit_by_prid]
        if len(members) <= 1:
            continue
        for m in members:
            assigned.add(m["prid"])
        blocks.append(
            {
                "prids": [m["prid"] for m in members],
                "start_time": min(m["start_time"] for m in members),
                "end_time": max(m["end_time"] for m in members),
                "duration": max(m["duration"] for m in members),
                "sort_key": min(m["requested_start_time"] for m in members),
            }
        )

    for visit in visits:
        if visit["prid"] in assigned:
            continue
        blocks.append(
            {
                "prids": [visit["prid"]],
                "start_time": visit["start_time"],
                "end_time": visit["end_time"],
                "duration": visit["duration"],
                "sort_key": visit["requested_start_time"],
            }
        )

    blocks.sort(key=lambda b: (b["sort_key"], b["prids"][0]))
    sequenced = sequence_patient_windows(
        [
            {
                "start_time": b["start_time"],
                "end_time": b["end_time"],
                "duration": b["duration"],
            }
            for b in blocks
        ],
        **options,
    )
    for i, block in enumerate(blocks):
        window = (sequenced[i]["start_time"], sequenced[i]["end_time"])
        for prid in block["prids"]:
            result[prid] = window
    return result


def floor_min_duration(
    duration: int,
    raw_min_duration: int | None,
    ratio: float = MIN_DURATION_FLOOR_RATIO,
) -> int:
    floor = int(duration * ratio)
    return max(raw_min_duration if raw_min_duration is not None else floor, floor)


def build_match_request_components(
    entries: list[dict[str, Any]],
) -> list[list[int]]:
    adjacency: dict[int, set[int]] = {}
    for e in entries:
        prid = e["prid"]
        adjacency.setdefault(prid, set())
        for other in e.get("match_request") or []:
            adjacency.setdefault(other, set())
            adjacency[prid].add(other)
            adjacency[other].add(prid)

    visited: set[int] = set()
    components: list[list[int]] = []
    for prid in adjacency:
        if prid in visited or not adjacency[prid]:
            continue
        stack = [prid]
        component: list[int] = []
        visited.add(prid)
        while stack:
            current = stack.pop()
            component.append(current)
            for nxt in adjacency[current]:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        if len(component) > 1:
            components.append(component)
    return components


def resolve_engine_patient_window_duration(
    window_start: int,
    window_end: int,
    requested_start: int,
    requested_end: int,
    requested_duration: int,
) -> tuple[int, int, int]:
    window_span = window_end - window_start
    if window_span > 0 and window_span >= requested_duration:
        return window_start, window_end, requested_duration

    requested_span = requested_end - requested_start
    if requested_span > 0:
        duration = min(requested_duration, requested_span)
        return requested_start, requested_end, duration

    if window_span > 0:
        duration = min(requested_duration, window_span)
        return window_start, window_end, duration

    return window_start, window_end, max(0, requested_duration)


def apply_roster_caregiver_specification(
    only_set: list[int],
    must_visit_patients: dict[str, float],
    has_only_links: bool,
    pinned_prids: list[int],
    cancelled_prids: set[int],
) -> tuple[list[int], dict[str, float]]:
    only = [p for p in only_set if p not in cancelled_prids]
    must = {k: v for k, v in must_visit_patients.items() if int(k) not in cancelled_prids}
    roster_prids = unique_sorted([p for p in pinned_prids if p not in cancelled_prids])
    if not roster_prids:
        return only, must
    if has_only_links:
        return unique_sorted(only + roster_prids), must
    for prid in roster_prids:
        must[str(prid)] = ROSTER_MUST_VISIT_WEIGHT
    return only, must


def apply_roster_patient_specification(
    patient: dict[str, Any],
    source: dict[str, Any],
    roster_visit: dict[str, Any] | None,
) -> dict[str, Any]:
    if not roster_visit:
        return patient

    duration = roster_visit["end_minute"] - roster_visit["start_minute"]
    if roster_visit["pinned"]:
        return {
            **patient,
            "start_time": roster_visit["start_minute"],
            "end_time": roster_visit["end_minute"],
            "duration": duration,
            "min_duration": duration,
            "fix_window": 1,
        }

    window_span = patient["end_time"] - patient["start_time"]
    uncapped_min = source.get("min_duration")
    if uncapped_min is None:
        uncapped_min = duration
    min_duration = min(uncapped_min, window_span) if window_span > 0 else uncapped_min

    matches_requested = (
        roster_visit["start_minute"] == source["requested_start_time"]
        and roster_visit["end_minute"] == source["requested_end_time"]
    )
    request_differs = (
        source["requested_start_time"] != source["history_start"]
        or source["requested_end_time"] != source["history_end"]
    )
    coordinator_adjusted = (
        roster_visit["start_minute"] != source["requested_start_time"]
        or roster_visit["end_minute"] != source["requested_end_time"]
        or duration != patient["duration"]
    )
    fix_window = (
        1
        if coordinator_adjusted or (matches_requested and request_differs)
        else patient.get("fix_window", 0)
    )
    return {
        **patient,
        "duration": duration,
        "min_duration": min_duration,
        "fix_window": fix_window,
    }


def normalize_execute_caregiver_entry(caregiver: dict[str, Any]) -> dict[str, Any]:
    only = caregiver.get("only_set") or []
    dislike = caregiver.get("dislike_set") or []
    return {
        **caregiver,
        "only_set": only if only else None,
        "dislike_set": dislike if dislike else None,
    }
