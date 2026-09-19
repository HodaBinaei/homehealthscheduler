from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.schemas.request import EngineJobStatusResponse
from app.services.engine_client import get_job

logger = logging.getLogger("hhs.jobs")

router = APIRouter(prefix="/api-data/v1/jobs", tags=["jobs"])


@router.get(
    "/{job_id}",
    response_model=EngineJobStatusResponse,
    dependencies=[Depends(require_api_key)],
)
async def get_engine_job(
    job_id: str,
    settings: Settings = Depends(get_settings),
) -> EngineJobStatusResponse:
    """Proxy engine-service job status / result."""
    try:
        body = await get_job(settings, job_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Job status proxy failed job_id=%s", job_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    progress_raw = body.get("progress_percent")
    progress_percent: int | None = None
    if progress_raw is not None and str(progress_raw).strip() != "":
        try:
            progress_percent = int(float(progress_raw))
        except (TypeError, ValueError):
            progress_percent = None

    progress_message = body.get("progress_message")
    if progress_message is not None:
        progress_message = str(progress_message)

    return EngineJobStatusResponse(
        job_id=str(body.get("job_id") or job_id),
        job_type=body.get("job_type"),
        status=body.get("status"),
        result=body.get("result"),
        error=body.get("error"),
        created_at=body.get("created_at"),
        started_at=body.get("started_at"),
        completed_at=body.get("completed_at"),
        progress_percent=progress_percent,
        progress_message=progress_message,
    )
