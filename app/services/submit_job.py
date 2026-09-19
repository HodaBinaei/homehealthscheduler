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
from app.services.runs_store import insert_run
from app.services.s3_store import S3PayloadStore

logger = logging.getLogger("hhs.submit_job")

AdapterFn = Callable[[dict[str, Any]], dict[str, Any]]

JOB_ADAPTERS: dict[str, AdapterFn] = {
    "full-assignment": to_full_assignment_request,
    "multicpsat": to_multicpsat_request,
    "reschedule": to_reschedule_request,
}


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
    Build payload, archive to S3, submit to engine-service.

    For multicpsat, pass visit_ids + provider_user_ids to process only that subset.
    Returns {run_id, job_id, job_type, status, date, caregiver_count, ...}.
    Raises on build/submit failure (after recording a FAILED run when possible).
    """
    adapter = JOB_ADAPTERS.get(job_type)
    if adapter is None:
        raise ValueError(f"Unknown job type: {job_type}")

    if job_type == "multicpsat":
        if not visit_ids or not provider_user_ids:
            raise ValueError(
                "multicpsat requires non-empty visitIds and providerUserIds"
            )

    logger.info(
        "Scheduler job started type=%s date=%s hour=%s visitIds=%s providerUserIds=%s",
        job_type,
        target_date,
        hour,
        visit_ids,
        provider_user_ids,
    )
    target = date.fromisoformat(target_date)
    run_id = str(uuid.uuid4())
    db = SessionLocal()
    s3 = S3PayloadStore(settings)
    s3_key: str | None = None
    caregiver_count = patient_count = feasible_count = 0

    try:
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
                insert_run(
                    db,
                    run_id=run_id,
                    roster_date=target,
                    hour=hour,
                    token=None,
                    status="FAILED",
                    error=f"S3 upload failed: {exc}",
                    caregiver_count=caregiver_count,
                    patient_count=patient_count,
                    feasible_count=feasible_count,
                    request_payload_s3_key=None,
                )
                raise
        else:
            logger.warning("Skipping S3 upload (AWS not configured)")

        try:
            accepted = submit_scheduler_job_sync(settings, job_type, payload)
            job_id = accepted["job_id"]
            insert_run(
                db,
                run_id=run_id,
                roster_date=target,
                hour=hour,
                token=job_id,
                status="SUBMITTED",
                caregiver_count=caregiver_count,
                patient_count=patient_count,
                feasible_count=feasible_count,
                request_payload_s3_key=s3_key,
            )
            logger.info(
                "Scheduler job finished type=%s date=%s job_id=%s s3_key=%s",
                job_type,
                target_date,
                job_id,
                s3_key,
            )
            return {
                "run_id": run_id,
                "job_id": job_id,
                "job_type": accepted.get("job_type") or job_type,
                "status": "accepted",
                "date": target_date,
                "engine_status": accepted.get("status"),
                "caregiver_count": caregiver_count,
                "patient_count": patient_count,
                "feasible_count": feasible_count,
            }
        except Exception as exc:
            insert_run(
                db,
                run_id=run_id,
                roster_date=target,
                hour=hour,
                token=None,
                status="FAILED",
                error=str(exc),
                caregiver_count=caregiver_count,
                patient_count=patient_count,
                feasible_count=feasible_count,
                request_payload_s3_key=s3_key,
            )
            raise
    finally:
        db.close()
