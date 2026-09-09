from __future__ import annotations

from datetime import date
from typing import Any

from app.services.payload.time_utils import (
    build_absolute_window,
    candidate_occurrence_dates,
    cap_absolute_window_at_date,
    clip_absolute_window_to_date,
    day_name_from_date,
    format_hhmm,
    has_exception_on_date,
    is_valid_minute_window,
    normalize_date_only,
    to_minutes_hhmm,
)
from app.services.payload.constants import MINUTES_IN_DAY


def find_schedule_occurrence_covering_date(schedule: dict[str, Any], target: date) -> date | None:
    prefs = schedule.get("preferences") or {}
    exceptions = schedule.get("exceptions") or []

    # Open-ended continuous unavailability
    if (
        prefs.get("is_temporary")
        and prefs.get("is_unavailability")
        and not prefs.get("effective_date_to")
        and max(0, schedule.get("end_time_date_offset_days") or 0) == 0
    ):
        from_d = normalize_date_only(prefs.get("effective_date_from"))
        if not from_d or target < from_d:
            return None
        if has_exception_on_date(exceptions, target.isoformat()):
            return None
        return from_d

    offset = max(0, schedule.get("end_time_date_offset_days") or 0)
    candidates = candidate_occurrence_dates(target, offset)
    temp_cap = (
        normalize_date_only(prefs.get("effective_date_to"))
        if prefs.get("is_temporary")
        else None
    )

    for candidate in candidates:
        day_name = day_name_from_date(candidate)
        days = schedule.get("days") or []
        if days and day_name not in days:
            continue

        if prefs.get("is_temporary"):
            from_d = normalize_date_only(prefs.get("effective_date_from"))
            to_d = normalize_date_only(prefs.get("effective_date_to"))
            if not from_d:
                continue
            if candidate < from_d:
                continue
            if to_d and candidate > to_d:
                continue
        else:
            start_date = normalize_date_only(schedule.get("start_date"))
            if start_date and candidate < start_date:
                continue
            end_date = normalize_date_only(schedule.get("end_date"))
            if end_date and candidate > end_date:
                continue
            if start_date:
                days_since = (candidate - start_date).days
                weeks_since = days_since // 7
                if weeks_since % (schedule.get("occurs_every") or 1) != 0:
                    continue

        if has_exception_on_date(exceptions, candidate.isoformat()):
            continue

        absolute = build_absolute_window(
            candidate,
            format_hhmm(schedule.get("requested_start_time")),
            format_hhmm(schedule.get("requested_end_time")),
            offset,
        )
        if temp_cap:
            absolute = cap_absolute_window_at_date(absolute, temp_cap)
        if clip_absolute_window_to_date(absolute, target):
            return candidate
    return None


def resolve_schedule_visit_window(schedule: dict[str, Any], target: date) -> tuple[int, int] | None:
    prefs = schedule.get("preferences") or {}
    if (
        prefs.get("is_temporary")
        and prefs.get("is_unavailability")
        and not prefs.get("effective_date_to")
        and max(0, schedule.get("end_time_date_offset_days") or 0) == 0
    ):
        occurrence = find_schedule_occurrence_covering_date(schedule, target)
        if not occurrence:
            return None
        if target == occurrence:
            start = to_minutes_hhmm(format_hhmm(schedule.get("requested_start_time")))
            if not is_valid_minute_window(start, MINUTES_IN_DAY):
                return None
            return start, MINUTES_IN_DAY
        return 0, MINUTES_IN_DAY

    occurrence = find_schedule_occurrence_covering_date(schedule, target)
    if not occurrence:
        if max(0, schedule.get("end_time_date_offset_days") or 0) == 0:
            start = to_minutes_hhmm(format_hhmm(schedule.get("requested_start_time")))
            end = to_minutes_hhmm(format_hhmm(schedule.get("requested_end_time")))
            if is_valid_minute_window(start, end):
                return start, end
        return None

    offset = max(0, schedule.get("end_time_date_offset_days") or 0)
    absolute = build_absolute_window(
        occurrence,
        format_hhmm(schedule.get("requested_start_time")),
        format_hhmm(schedule.get("requested_end_time")),
        offset,
    )
    if prefs.get("is_temporary"):
        to_d = normalize_date_only(prefs.get("effective_date_to"))
        if to_d:
            absolute = cap_absolute_window_at_date(absolute, to_d)
    clipped = clip_absolute_window_to_date(absolute, target)
    if not clipped or not is_valid_minute_window(clipped[0], clipped[1]):
        return None
    return clipped


def resolve_schedule_flexibility_window(
    schedule: dict[str, Any], target: date
) -> tuple[int, int] | None:
    scheduled = resolve_schedule_visit_window(schedule, target)
    if not scheduled:
        return None
    prefs = schedule.get("preferences") or {}
    if not prefs.get("window_start") and not prefs.get("window_end"):
        return scheduled
    start = (
        to_minutes_hhmm(format_hhmm(prefs.get("window_start")))
        if prefs.get("window_start")
        else scheduled[0]
    )
    end = (
        to_minutes_hhmm(format_hhmm(prefs.get("window_end")))
        if prefs.get("window_end")
        else scheduled[1]
    )
    if not is_valid_minute_window(start, end):
        return scheduled
    return start, end


def find_availability_occurrence(
    slot: dict[str, Any], target: date
) -> date | None:
    offset = max(0, slot.get("end_time_date_offset_days") or 0)
    candidates = candidate_occurrence_dates(target, offset)
    prefs = slot.get("preferences") or {}
    exceptions = slot.get("exceptions") or []
    temp_cap = (
        normalize_date_only(prefs.get("effective_date_to"))
        if prefs.get("is_temporary")
        else None
    )

    for candidate in candidates:
        day_name = day_name_from_date(candidate)
        days = slot.get("days") or []
        if days and day_name not in days:
            continue

        if prefs.get("is_temporary"):
            from_d = normalize_date_only(prefs.get("effective_date_from"))
            to_d = normalize_date_only(prefs.get("effective_date_to"))
            if not from_d:
                continue
            if candidate < from_d:
                continue
            if to_d and candidate > to_d:
                continue
        else:
            start_date = normalize_date_only(slot.get("start_date"))
            if not start_date or candidate < start_date:
                continue
            end_date = normalize_date_only(slot.get("end_date"))
            if end_date and candidate > end_date:
                continue
            days_since = (candidate - start_date).days
            weeks_since = days_since // 7
            if weeks_since % (slot.get("occurs_every") or 1) != 0:
                continue

        if has_exception_on_date(exceptions, candidate.isoformat()):
            continue

        absolute = build_absolute_window(
            candidate,
            format_hhmm(slot.get("start_time")) if slot.get("start_time") is not None else "00:00",
            format_hhmm(slot.get("end_time")) if slot.get("end_time") is not None else "24:00",
            offset,
        )
        if temp_cap:
            absolute = cap_absolute_window_at_date(absolute, temp_cap)
        if clip_absolute_window_to_date(absolute, target):
            return candidate
    return None


def resolve_availability_window(slot: dict[str, Any], target: date) -> tuple[int, int] | None:
    occurrence = find_availability_occurrence(slot, target)
    if not occurrence:
        return None
    offset = max(0, slot.get("end_time_date_offset_days") or 0)
    absolute = build_absolute_window(
        occurrence,
        format_hhmm(slot.get("start_time")),
        format_hhmm(slot.get("end_time")),
        offset,
    )
    prefs = slot.get("preferences") or {}
    if prefs.get("is_temporary"):
        to_d = normalize_date_only(prefs.get("effective_date_to"))
        if to_d:
            absolute = cap_absolute_window_at_date(absolute, to_d)
    return clip_absolute_window_to_date(absolute, target)
