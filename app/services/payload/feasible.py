from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.services.payload import queries
from app.services.payload.time_utils import feasible_pair_day_index


def prefer_day_specific_weights(
    pairs: list[dict[str, Any]], day_index: int
) -> dict[tuple[int, int], float]:
    """Prefer day-specific weight over null-day for the same (cgid, client_id)."""
    result: dict[tuple[int, int], float] = {}
    null_day: dict[tuple[int, int], float] = {}
    for p in pairs:
        key = (int(p["cgid"]), int(p["client_id"]))
        dow = p.get("day_of_week")
        weight = float(p["weight"])
        if dow is None:
            null_day[key] = weight
        elif int(dow) == day_index:
            result[key] = weight
    for key, weight in null_day.items():
        if key not in result:
            result[key] = weight
    return result


def build_crid_prid_feasible(
    db: Session,
    users_output: dict[str, dict[str, Any]],
    clients_output: dict[str, dict[str, Any]],
    target: date,
) -> list[dict[str, Any]]:
    cid_to_crids: dict[int, list[int]] = {}
    for entry in users_output.values():
        cid_to_crids.setdefault(int(entry["cid"]), []).append(int(entry["crid"]))

    pid_to_prids: dict[int, list[int]] = {}
    for entry in clients_output.values():
        pid_to_prids.setdefault(int(entry["pid"]), []).append(int(entry["prid"]))

    caregiver_ids = list(cid_to_crids.keys())
    client_ids = list(pid_to_prids.keys())
    day_index = feasible_pair_day_index(target)
    pairs = queries.load_feasible_pairs(db, caregiver_ids, client_ids, day_index)
    weights = prefer_day_specific_weights(pairs, day_index)

    result: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for (cgid, client_id), weight in weights.items():
        for crid in cid_to_crids.get(cgid, []):
            for prid in pid_to_prids.get(client_id, []):
                key = (crid, prid)
                if key in seen:
                    continue
                seen.add(key)
                result.append({"crid": crid, "prid": prid, "weight": weight})
    return result
