from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.schemas.request import ScheduleExecuteRequest, ScheduleJobResponse
from app.services.submit_job import accept_scheduler_job, process_scheduler_job

logger = logging.getLogger("hhs.optimize")

router = APIRouter(prefix="/api-data/v1/optimize", tags=["optimize"])


@router.post(
    "",
    response_model=ScheduleJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
def create_optimize(
    body: ScheduleExecuteRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
) -> ScheduleJobResponse:
    """Accept reschedule immediately; build + engine submit run in background."""
    try:
        accepted = accept_scheduler_job(
            job_type="reschedule",
            target_date=body.date.isoformat(),
            hour=body.hour,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    background_tasks.add_task(
        process_scheduler_job,
        run_id=accepted["run_id"],
        job_id=accepted["job_id"],
        job_type="reschedule",
        target_date=accepted["date"],
        hour=body.hour,
        settings=settings,
    )
    logger.info(
        "Optimize accepted date=%s job_id=%s run_id=%s (background)",
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
