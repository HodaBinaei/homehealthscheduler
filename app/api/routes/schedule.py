from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.db.session import get_db
from app.schemas.request import (
    EngineRunItem,
    EngineRunsResponse,
    PayloadDownloadResponse,
    ScheduleExecuteRequest,
    ScheduleJobResponse,
)
from app.services.runs_store import get_run_by_id, get_run_by_token, list_runs
from app.services.s3_store import S3PayloadStore
from app.services.submit_job import run_scheduler_job

logger = logging.getLogger("hhs.schedule")

router = APIRouter(prefix="/api-data/v1/schedule", tags=["schedule"])


@router.post(
    "",
    response_model=ScheduleJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
def create_schedule(
    body: ScheduleExecuteRequest,
    settings: Settings = Depends(get_settings),
) -> ScheduleJobResponse:
    """Build full-assignment payload and submit to engine-service."""
    try:
        result = run_scheduler_job(
            job_type="full-assignment",
            target_date=body.date.isoformat(),
            hour=body.hour,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Schedule job failed date=%s", body.date.isoformat())
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


@router.get(
    "/runs",
    response_model=EngineRunsResponse,
    dependencies=[Depends(require_api_key)],
)
def get_runs(
    date_filter: date | None = Query(default=None, alias="date"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> EngineRunsResponse:
    """List engine runs (id, job_id as token, S3 key, counts, status)."""
    runs = list_runs(db, roster_date=date_filter, limit=limit)
    return EngineRunsResponse(runs=runs, count=len(runs))


@router.get(
    "/runs/{token}",
    response_model=EngineRunItem,
    dependencies=[Depends(require_api_key)],
)
def get_run(
    token: str,
    db: Session = Depends(get_db),
) -> EngineRunItem:
    """Get one engine run by engine job_id (stored as token)."""
    run = get_run_by_token(db, token)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return EngineRunItem(**run)


@router.get(
    "/runs/{token}/payload",
    response_model=PayloadDownloadResponse,
    dependencies=[Depends(require_api_key)],
)
def download_run_payload(
    token: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PayloadDownloadResponse:
    """Return a short-lived S3 presigned URL for the payload sent to the engine."""
    run = get_run_by_token(db, token)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    s3_key = run.get("request_payload_s3_key")
    if not s3_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No S3 payload stored for this run",
        )
    s3 = S3PayloadStore(settings)
    if not s3.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="S3 is not configured",
        )
    try:
        url = s3.presigned_download_url(s3_key)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create download URL: {exc}",
        ) from exc
    return PayloadDownloadResponse(
        id=run["id"],
        token=run.get("token"),
        roster_date=run["roster_date"],
        request_payload_s3_key=s3_key,
        download_url=url,
        expires_in_seconds=settings.s3_presign_expires_seconds,
    )


@router.get(
    "/run-by-id/{run_id}/payload",
    response_model=PayloadDownloadResponse,
    dependencies=[Depends(require_api_key)],
)
def download_run_payload_by_id(
    run_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PayloadDownloadResponse:
    """Return a short-lived S3 presigned URL using the internal run id."""
    run = get_run_by_id(db, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    s3_key = run.get("request_payload_s3_key")
    if not s3_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No S3 payload stored for this run",
        )
    s3 = S3PayloadStore(settings)
    if not s3.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="S3 is not configured",
        )
    try:
        url = s3.presigned_download_url(s3_key)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create download URL: {exc}",
        ) from exc
    return PayloadDownloadResponse(
        id=run["id"],
        token=run.get("token"),
        roster_date=run["roster_date"],
        request_payload_s3_key=s3_key,
        download_url=url,
        expires_in_seconds=settings.s3_presign_expires_seconds,
    )
