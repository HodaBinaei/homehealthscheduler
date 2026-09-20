"""Shared build → S3 → engine submit flow for schedule / multi-schedule / optimize."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any, Callable

from app.config import Settings
from app.db.session import SessionLocal
from app.services.engine_client import submit_scheduler_job_sync
from app.services.payload.builder import build_day_bundle_for_engine
from app.services.payload.engine_adapter import (
    to_full_assignment_request,
    to_multicpsat_request,
    to_reschedule_request,
)
from app.services.payload.subset_filter import (
    filter_day_bundle_by_selection,
    resolve_prids_for_visit_ids,
)
from app.services.runs_store import insert_run, update_run
from app.services.s3_store import S3PayloadStore

logger = logging.getLogger("hhs.submit_job")

AdapterFn = Callable[[dict[str, Any]], dict[str, Any]]

JOB_ADAPTERS: dict[str, AdapterFn] = {
    "full-assignment": to_full_assignment_request,
    "multicpsat": to_multicpsat_request,
    "reschedule": to_reschedule_request,
}

# Local run statuses (Panel polls bridge job_id = token until engine finishes)
STATUS_PENDING = "PENDING"
STATUS_BUILDING = "BUILDING"
STATUS_SUBMITTED = "SUBMITTED"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"


def accept_scheduler_job(
    *,
    job_type: str,
    target_date: str,
    hour: int,
    visit_ids: list[str] | None = None,
    provider_user_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    Validate request + persist a PENDING run, then return immediately.

    `job_id` (== token) is stable for Panel polling for the life of the run.
    """
    if job_type not in JOB_ADAPTERS:
        raise ValueError(f"Unknown job type: {job_type}")
    if job_type == "multicpsat":
        if not visit_ids or not provider_user_ids:
            raise ValueError(
                "multicpsat requires non-empty visitIds and providerUserIds"
            )

    target = date.fromisoformat(target_date)
    run_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        insert_run(
            db,
            run_id=run_id,
            roster_date=target,
            hour=hour,
            token=job_id,
            status=STATUS_PENDING,
            job_type=job_type,
        )
    finally:
        db.close()

    logger.info(
        "Scheduler job accepted type=%s date=%s hour=%s job_id=%s run_id=%s",
        job_type,
        target_date,
        hour,
        job_id,
        run_id,
    )
    return {
        "status": "accepted",
        "date": target_date,
        "job_id": job_id,
        "job_type": job_type,
        "run_id": run_id,
        "hour": hour,
        "visit_ids": list(visit_ids or []),
        "provider_user_ids": list(provider_user_ids or []),
    }


def process_scheduler_job(
    *,
    run_id: str,
    job_id: str,
    job_type: str,
    target_date: str,
    hour: int,
    settings: Settings,
    visit_ids: list[str] | None = None,
    provider_user_ids: list[int] | None = None,
) -> None:
    """Background: build payload, upload S3, submit to engine-service."""
    adapter = JOB_ADAPTERS[job_type]
    target = date.fromisoformat(target_date)
    db = SessionLocal()
    s3 = S3PayloadStore(settings)
    s3_key: str | None = None
    caregiver_count = patient_count = feasible_count = 0

    try:
        update_run(db, run_id, status=STATUS_BUILDING, clear_error=True)
        logger.info(
            "Scheduler job building type=%s date=%s job_id=%s run_id=%s",
            job_type,
            target_date,
            job_id,
            run_id,
        )

        bundle = build_day_bundle_for_engine(db, target)
        if job_type == "multicpsat":
            roster = bundle.get("roster") or {}
            prid_by_slot = roster.get("prid_by_slot") or {}
            prids = resolve_prids_for_visit_ids(
                db,
                target,
                list(visit_ids or []),
                prid_by_slot=prid_by_slot,
            )
            bundle = filter_day_bundle_by_selection(
                bundle,
                provider_user_ids=list(provider_user_ids or []),
                prids=prids,
            )
        payload = adapter(bundle)
        caregiver_count = len(payload.get("caregiver_dict") or [])
        patient_count = len(payload.get("patient_dict") or [])
        feasible_count = len(payload.get("crid_prid_feasible_dict") or [])
        logger.info(
            "Scheduler job built type=%s date=%s caregivers=%s patients=%s feasible=%s",
            job_type,
            target_date,
            caregiver_count,
            patient_count,
            feasible_count,
        )

        if s3.enabled:
            s3_key = s3.build_key(target_date, run_id)
            try:
                s3.upload_payload(s3_key, payload)
            except Exception as exc:
                logger.exception("S3 upload failed date=%s run_id=%s", target_date, run_id)
                update_run(
                    db,
                    run_id,
                    status=STATUS_FAILED,
                    error=f"S3 upload failed: {exc}",
                    caregiver_count=caregiver_count,
                    patient_count=patient_count,
                    feasible_count=feasible_count,
                )
                return
        else:
            logger.warning("Skipping S3 upload (AWS not configured)")

        accepted = submit_scheduler_job_sync(settings, job_type, payload)
        engine_job_id = str(accepted["job_id"])
        update_run(
            db,
            run_id,
            status=STATUS_SUBMITTED,
            clear_error=True,
            engine_job_id=engine_job_id,
            caregiver_count=caregiver_count,
            patient_count=patient_count,
            feasible_count=feasible_count,
            request_payload_s3_key=s3_key,
        )
        logger.info(
            "Scheduler job submitted type=%s date=%s bridge_job_id=%s engine_job_id=%s s3_key=%s",
            job_type,
            target_date,
            job_id,
            engine_job_id,
            s3_key,
        )
    except Exception as exc:
        logger.exception(
            "Scheduler job failed type=%s date=%s job_id=%s run_id=%s",
            job_type,
            target_date,
            job_id,
            run_id,
        )
        try:
            update_run(
                db,
                run_id,
                status=STATUS_FAILED,
                error=str(exc),
                caregiver_count=caregiver_count,
                patient_count=patient_count,
                feasible_count=feasible_count,
                request_payload_s3_key=s3_key,
            )
        except Exception:
            logger.exception("Failed to persist FAILED status run_id=%s", run_id)
    finally:
        db.close()


def run_scheduler_job(
    *,
    job_type: str,
    target_date: str,
    hour: int,
    settings: Settings,
    visit_ids: list[str] | None = None,
    provider_user_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    Synchronous path (tests / tools): accept + process in-process.

    HTTP routes should use accept_scheduler_job + BackgroundTasks instead.
    """
    accepted = accept_scheduler_job(
        job_type=job_type,
        target_date=target_date,
        hour=hour,
        visit_ids=visit_ids,
        provider_user_ids=provider_user_ids,
    )
    process_scheduler_job(
        run_id=accepted["run_id"],
        job_id=accepted["job_id"],
        job_type=job_type,
        target_date=target_date,
        hour=hour,
        settings=settings,
        visit_ids=visit_ids,
        provider_user_ids=provider_user_ids,
    )
    return accepted
