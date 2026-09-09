from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.services.payload.constants import DAY_NAMES, MINUTES_IN_DAY


def normalize_date_only(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def to_date_only_string(value: date | datetime | str) -> str:
    d = normalize_date_only(value)
    if d is None:
        raise ValueError("Invalid date format. Use ISO date string (YYYY-MM-DD).")
    return d.isoformat()


def add_days(d: date, days: int) -> date:
    return d + timedelta(days=days)


def format_hhmm(time_value: Any) -> str:
    if time_value is None:
        return "00:00"
    text = str(time_value)
    return text[:5]


def to_minutes_hhmm(hhmm: str) -> int:
    parts = format_hhmm(hhmm).split(":")
    return int(parts[0]) * 60 + int(parts[1])


def minute_to_hhmm(minute: int) -> str:
    clamped = max(0, min(MINUTES_IN_DAY, minute))
    h = clamped // 60
    m = clamped % 60
    return f"{h:02d}:{m:02d}"


def day_name_from_date(d: date) -> str:
    # date.weekday(): Mon=0 … Sun=6 → JS getUTCDay Sunday=0
    js_day = (d.weekday() + 1) % 7
    return DAY_NAMES[js_day]


def feasible_pair_day_index(d: date) -> int:
    """Monday=0 … Sunday=6."""
    return d.weekday()


def normalize_engine_time_window(start_time: int, end_time: int) -> tuple[int, int]:
    if start_time == 0 and end_time == 0:
        return 0, 1439
    return start_time, end_time


def build_absolute_window(
    occurrence: date,
    start_time: str,
    end_time: str,
    end_time_date_offset_days: int,
) -> tuple[datetime, datetime]:
    start_m = to_minutes_hhmm(start_time)
    end_m = to_minutes_hhmm(end_time)
    offset = max(0, end_time_date_offset_days or 0)
    start_dt = datetime(occurrence.year, occurrence.month, occurrence.day, tzinfo=timezone.utc) + timedelta(
        minutes=start_m
    )
    end_day = add_days(occurrence, offset)
    end_dt = datetime(end_day.year, end_day.month, end_day.day, tzinfo=timezone.utc) + timedelta(minutes=end_m)
    return start_dt, end_dt


def clip_absolute_window_to_date(
    absolute: tuple[datetime, datetime],
    target: date,
) -> tuple[int, int] | None:
    day_start = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(minutes=MINUTES_IN_DAY)
    start_ms = max(absolute[0], day_start)
    end_ms = min(absolute[1], day_end)
    if start_ms >= end_ms:
        return None
    start_minute = int(round((start_ms - day_start).total_seconds() / 60))
    end_minute = int(round((end_ms - day_start).total_seconds() / 60))
    return start_minute, end_minute


def cap_absolute_window_at_date(
    absolute: tuple[datetime, datetime],
    cap: date,
) -> tuple[datetime, datetime]:
    cap_end = datetime(cap.year, cap.month, cap.day, tzinfo=timezone.utc) + timedelta(minutes=MINUTES_IN_DAY)
    return absolute[0], min(absolute[1], cap_end)


def candidate_occurrence_dates(target: date, end_time_date_offset_days: int) -> list[date]:
    offset = max(0, end_time_date_offset_days or 0)
    return [add_days(target, -back) for back in range(offset + 1)]


def has_exception_on_date(exceptions: list[Any], date_string: str) -> bool:
    for ex in exceptions or []:
        ex_date = normalize_date_only(ex)
        if ex_date and ex_date.isoformat() == date_string:
            return True
    return False


def is_valid_minute_window(start_minute: int, end_minute: int) -> bool:
    if start_minute >= end_minute:
        return False
    return 0 <= start_minute <= MINUTES_IN_DAY and end_minute <= MINUTES_IN_DAY


def subtract_blocking(
    start: int,
    end: int,
    blocking: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Subtract blocking intervals from [start, end); return remaining segments."""
    segments = [(start, end)]
    for b_start, b_end in sorted(blocking):
        next_segments: list[tuple[int, int]] = []
        for s, e in segments:
            if b_end <= s or b_start >= e:
                next_segments.append((s, e))
                continue
            if b_start > s:
                next_segments.append((s, b_start))
            if b_end < e:
                next_segments.append((b_end, e))
        segments = next_segments
    return [(s, e) for s, e in segments if e > s]
