from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, status

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.db.session import SessionLocal
from app.schemas.request import ScheduleExecuteRequest, ScheduleExecuteResponse
from app.services.engine_client import submit_execute_sync
from app.services.payload.builder import build_execute_payload_for_date

logger = logging.getLogger("hhs.schedule")

router = APIRouter(prefix="/api/v1/schedule", tags=["schedule"])


def _prepare_data_job(target_date: str, hour: int, settings: Settings) -> None:
    """Build payload and submit to engine; runs after 202 is returned."""
    logger.info("Background prepare-data started date=%s hour=%s", target_date, hour)
    target = date.fromisoformat(target_date)
    db = SessionLocal()
    try:
        payload = build_execute_payload_for_date(db, target)
        data = payload.get("data") or {}
        logger.info(
            "Background prepare-data built date=%s caregivers=%s patients=%s feasible=%s",
            target_date,
            len(data.get("caregivers") or {}),
            len(data.get("patient") or {}),
            len(data.get("crid_prid_feasible") or []),
        )
        submit_execute_sync(settings, payload)
        logger.info("Background prepare-data finished date=%s", target_date)
    except Exception:
        logger.exception("Background prepare-data failed date=%s hour=%s", target_date, hour)
    finally:
        db.close()


@router.post(
    "/prepare-data",
    response_model=ScheduleExecuteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
async def prepare_data(
    body: ScheduleExecuteRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
) -> ScheduleExecuteResponse:
    # hour accepted for forward compatibility; unused in v1
    background_tasks.add_task(
        _prepare_data_job,
        body.date.isoformat(),
        body.hour,
        settings,
    )
    logger.info(
        "Accepted prepare-data date=%s hour=%s (processing in background)",
        body.date.isoformat(),
        body.hour,
    )
    return ScheduleExecuteResponse(status="accepted", date=body.date.isoformat())
