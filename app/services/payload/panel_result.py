"""Normalize engine Schedule results into Panel-friendly shapes.

Engine / HHS wire format uses string ids and `*_list` field names.
Panel ingest reads assignments from caregiver_schedules; we still normalize
ids/aliases and backfill assigned_* lists when multicpsat leaves them empty.
"""

from __future__ import annotations

from typing import Any


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip() != "":
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
    return None


def _as_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        n = _as_int(item)
        if n is not None:
            out.append(n)
    return out


def _first_list(payload: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        if key in payload and payload[key] is not None:
            value = payload[key]
            return value if isinstance(value, list) else []
    return []


def _assignments_from_schedules(
    schedules: dict[str, Any],
) -> tuple[list[int], list[int]]:
    """
    Derive assigned prid/crid lists from caregiver_schedules visits.

    multicpsat sometimes returns visits under schedules while leaving
    assigned_prid_list / assigned_crid_list empty. Keep lists aligned for
    audits/aliases (Panel apply already reads caregiver_schedules).
    """
    prids: list[int] = []
    crids: list[int] = []
    seen_prids: set[int] = set()
    seen_crids: set[int] = set()

    for key, entry in schedules.items():
        if not isinstance(entry, dict):
            continue
        crid = _as_int(entry.get("crid"))
        if crid is None:
            crid = _as_int(key)

        visits = entry.get("visits")
        if not isinstance(visits, list):
            visits = entry.get("assignment")
        if not isinstance(visits, list) or not visits:
            continue

        has_assigned_visit = False
        for visit in visits:
            if not isinstance(visit, dict):
                continue
            prid = _as_int(visit.get("prid"))
            if prid is None:
                continue
            has_assigned_visit = True
            if prid not in seen_prids:
                seen_prids.add(prid)
                prids.append(prid)

        if has_assigned_visit and crid is not None and crid not in seen_crids:
            seen_crids.add(crid)
            crids.append(crid)

    return prids, crids


def normalize_schedule_result_for_panel(result: Any) -> Any:
    """
    Attach Panel aliases and coerce ids to ints on a Schedule-shaped result.

    Also backfills assigned_prid/crid lists from caregiver_schedules when the
    engine left those lists empty but still placed visits under schedules.

    Leaves unknown shapes untouched. Safe to call on None / non-dicts.
    """
    if not isinstance(result, dict):
        return result

    # Nested engine wrappers: { result: Schedule }
    if (
        "result" in result
        and isinstance(result.get("result"), dict)
        and "caregiver_schedules" not in result
    ):
        return {
            **result,
            "result": normalize_schedule_result_for_panel(result["result"]),
        }

    if "caregiver_schedules" not in result and "assigned_prid_list" not in result:
        # Not a Schedule-like payload
        if any(k in result for k in ("assigned_prids", "unassigned_prids", "caregiver_schedules")):
            pass
        else:
            return result

    out = dict(result)

    assigned_prids = _as_int_list(
        _first_list(out, "assigned_prids", "assigned_prid_list")
    )
    unassigned_prids = _as_int_list(
        _first_list(out, "unassigned_prids", "unassigned_prid_list")
    )
    removed_caregivers = _as_int_list(
        _first_list(out, "removed_caregivers", "removed_crid_list")
    )
    unassigned_crids = _as_int_list(
        _first_list(out, "unassigned_crids", "unassigned_crid_list")
    )
    assigned_crids = _as_int_list(
        _first_list(out, "assigned_crids", "assigned_crid_list")
    )

    schedules = out.get("caregiver_schedules")
    if isinstance(schedules, dict):
        normalized_schedules: dict[str, Any] = {}
        for key, entry in schedules.items():
            if not isinstance(entry, dict):
                normalized_schedules[str(key)] = entry
                continue
            cg = dict(entry)
            cid = _as_int(cg.get("cid"))
            crid = _as_int(cg.get("crid"))
            if cid is not None:
                cg["cid"] = cid
            if crid is not None:
                cg["crid"] = crid

            for visit_key in ("visits", "assignment"):
                visits = cg.get(visit_key)
                if not isinstance(visits, list):
                    continue
                normalized_visits = []
                for visit in visits:
                    if not isinstance(visit, dict):
                        normalized_visits.append(visit)
                        continue
                    v = dict(visit)
                    for field in ("prid", "pid", "crid", "start_time", "end_time", "duration"):
                        if field in v:
                            n = _as_int(v.get(field))
                            if n is not None:
                                v[field] = n
                    normalized_visits.append(v)
                cg[visit_key] = normalized_visits

            normalized_schedules[str(key)] = cg
        out["caregiver_schedules"] = normalized_schedules

        derived_prids, derived_crids = _assignments_from_schedules(normalized_schedules)
        if not assigned_prids and derived_prids:
            assigned_prids = derived_prids
        elif derived_prids:
            # Union preserving existing order, then any missing from schedules
            seen = set(assigned_prids)
            for p in derived_prids:
                if p not in seen:
                    assigned_prids.append(p)
                    seen.add(p)
        if not assigned_crids and derived_crids:
            assigned_crids = derived_crids
        elif derived_crids:
            seen_c = set(assigned_crids)
            for c in derived_crids:
                if c not in seen_c:
                    assigned_crids.append(c)
                    seen_c.add(c)

        # Drop assigned prids from unassigned if they appear on a schedule
        if assigned_prids and unassigned_prids:
            assigned_set = set(assigned_prids)
            unassigned_prids = [p for p in unassigned_prids if p not in assigned_set]
        if assigned_crids and unassigned_crids:
            assigned_c_set = set(assigned_crids)
            unassigned_crids = [c for c in unassigned_crids if c not in assigned_c_set]

    out["assigned_prids"] = assigned_prids
    out["assigned_prid_list"] = [str(p) for p in assigned_prids]
    out["unassigned_prids"] = unassigned_prids
    out["unassigned_prid_list"] = [str(p) for p in unassigned_prids]
    out["removed_caregivers"] = removed_caregivers
    out["removed_crid_list"] = [str(c) for c in removed_caregivers]
    out["unassigned_crids"] = unassigned_crids
    out["unassigned_crid_list"] = [str(c) for c in unassigned_crids]
    out["assigned_crids"] = assigned_crids
    out["assigned_crid_list"] = [str(c) for c in assigned_crids]

    return out
