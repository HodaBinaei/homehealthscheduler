from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.schemas.request import ScheduleExecuteRequest, ScheduleJobResponse
from app.services.submit_job import run_scheduler_job

logger = logging.getLogger("hhs.multi_schedule")

router = APIRouter(prefix="/api-data/v1/multi-schedule", tags=["multi-schedule"])


@router.post(
    "",
    response_model=ScheduleJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
def create_multi_schedule(
    body: ScheduleExecuteRequest,
    settings: Settings = Depends(get_settings),
) -> ScheduleJobResponse:
    """Build multicpsat payload and submit to engine-service."""
    try:
        result = run_scheduler_job(
            job_type="multicpsat",
            target_date=body.date.isoformat(),
            hour=body.hour,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Multi-schedule job failed date=%s", body.date.isoformat())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return ScheduleJobResponse(
        status="accepted",
        date=result["date"],
        job_id=result["job_id"],
        job_type=result["job_type"],
        run_id=result.get("run_id"),
    )
