from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.schemas.request import MultiScheduleExecuteRequest, ScheduleJobResponse
from app.services.submit_job import accept_scheduler_job, process_scheduler_job

logger = logging.getLogger("hhs.multi_schedule")

router = APIRouter(prefix="/api-data/v1/multi-schedule", tags=["multi-schedule"])


@router.post(
    "",
    response_model=ScheduleJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
def create_multi_schedule(
    body: MultiScheduleExecuteRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
) -> ScheduleJobResponse:
    """Accept multicpsat immediately; build + engine submit run in background."""
    try:
        accepted = accept_scheduler_job(
            job_type="multicpsat",
            target_date=body.date.isoformat(),
            hour=body.hour,
            visit_ids=[str(v) for v in body.visitIds],
            provider_user_ids=body.providerUserIds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    background_tasks.add_task(
        process_scheduler_job,
        run_id=accepted["run_id"],
        job_id=accepted["job_id"],
        job_type="multicpsat",
        target_date=accepted["date"],
        hour=body.hour,
        settings=settings,
        visit_ids=[str(v) for v in body.visitIds],
        provider_user_ids=body.providerUserIds,
    )
    logger.info(
        "Multi-schedule accepted date=%s job_id=%s run_id=%s (background)",
        accepted["date"],
        accepted["job_id"],
        accepted["run_id"],
    )
    return ScheduleJobResponse(
        status="accepted",
        date=accepted["date"],
        job_id=accepted["job_id"],
        job_type=accepted["job_type"],
        run_id=accepted["run_id"],
    )
