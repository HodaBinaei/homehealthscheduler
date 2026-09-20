"""Stop / cancel a bridge job (and upstream engine job when submitted)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.services.engine_client import cancel_job as cancel_engine_job
from app.services.runs_store import get_run_by_job_ref, update_run
from app.services.submit_job import (
    STATUS_BUILDING,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUBMITTED,
)

logger = logging.getLogger("hhs.stop_job")

_STOPPABLE = frozenset(
    {STATUS_PENDING, STATUS_BUILDING, STATUS_SUBMITTED}
)


async def stop_bridge_job(
    *,
    db: Session,
    settings: Settings,
    job_ref: str,
) -> dict[str, Any]:
    """
    Mark the bridge run cancelled and ask engine-service to cancel when known.

    Returns a Panel-facing job status payload (status=cancelled).
    """
    run = get_run_by_job_ref(db, job_ref)
    if run is None:
        # Legacy: treat job_ref as a raw engine id
        try:
            body = await cancel_engine_job(settings, job_ref)
        except LookupError as exc:
            raise LookupError(str(exc)) from exc
        return {
            "job_id": job_ref,
            "job_type": body.get("job_type"),
            "status": "cancelled",
            "result": None,
            "error": body.get("error") or "Cancelled by user",
            "created_at": body.get("created_at"),
            "started_at": body.get("started_at"),
            "completed_at": body.get("completed_at"),
            "progress_percent": 0,
            "progress_message": "cancelled",
        }

    token = str(run.get("token") or job_ref)
    status_raw = (run.get("status") or STATUS_PENDING).upper()
    engine_job_id = run.get("engine_job_id")

    if status_raw == STATUS_CANCELLED:
        return {
            "job_id": token,
            "job_type": run.get("job_type"),
            "status": "cancelled",
            "result": None,
            "error": run.get("error") or "Cancelled by user",
            "created_at": run.get("created_at"),
            "started_at": run.get("updated_at"),
            "completed_at": run.get("updated_at"),
            "progress_percent": 0,
            "progress_message": "cancelled",
        }

    if status_raw == STATUS_FAILED:
        return {
            "job_id": token,
            "job_type": run.get("job_type"),
            "status": "failed",
            "result": None,
            "error": run.get("error") or "Job already failed",
            "created_at": run.get("created_at"),
            "started_at": run.get("updated_at"),
            "completed_at": run.get("updated_at"),
            "progress_percent": 0,
            "progress_message": "failed",
        }

    engine_error: str | None = None
    if engine_job_id:
        try:
            await cancel_engine_job(settings, str(engine_job_id))
        except LookupError:
            logger.info(
                "Engine job %s already gone while stopping bridge token %s",
                engine_job_id,
                token,
            )
        except Exception as exc:  # noqa: BLE001 — still mark bridge cancelled
            engine_error = str(exc)
            logger.warning(
                "Engine cancel failed for %s (bridge %s): %s",
                engine_job_id,
                token,
                exc,
            )
    elif status_raw not in _STOPPABLE and status_raw != STATUS_SUBMITTED:
        # Unexpected local status — still force cancel to unlock Panel.
        logger.warning(
            "Stopping bridge run %s with unusual status=%s",
            run.get("id"),
            status_raw,
        )

    error_msg = "Cancelled by user"
    if engine_error:
        error_msg = f"Cancelled by user (engine cancel warning: {engine_error})"

    updated = update_run(
        db,
        str(run["id"]),
        status=STATUS_CANCELLED,
        error=error_msg,
    )

    return {
        "job_id": token,
        "job_type": (updated or run).get("job_type"),
        "status": "cancelled",
        "result": None,
        "error": error_msg,
        "created_at": (updated or run).get("created_at"),
        "started_at": (updated or run).get("updated_at"),
        "completed_at": (updated or run).get("updated_at"),
        "progress_percent": 0,
        "progress_message": "cancelled",
    }
