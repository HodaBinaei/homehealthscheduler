from __future__ import annotations

import asyncio
import logging
from secrets import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.schemas.request import EngineJobStatusResponse
from app.services.engine_client import get_job, get_job_logs

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


@router.get(
    "/{job_id}/logs",
    dependencies=[Depends(require_api_key)],
)
async def get_engine_job_logs(
    job_id: str,
    after: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Proxy engine-service job log backlog."""
    try:
        return await get_job_logs(settings, job_id, after=after)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Job logs proxy failed job_id=%s", job_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


def _bridge_api_key_ok(websocket: WebSocket, settings: Settings) -> bool:
    provided = websocket.headers.get("x-api-key") or websocket.query_params.get("api_key")
    if not provided or not settings.api_key:
        return False
    try:
        return compare_digest(provided, settings.api_key)
    except (TypeError, ValueError):
        return False


@router.websocket("/{job_id}/logs/ws")
async def proxy_engine_job_logs_ws(
    websocket: WebSocket,
    job_id: str,
    settings: Settings = Depends(get_settings),
) -> None:
    """Proxy live engine-service job log WebSocket to Panel clients."""
    if not _bridge_api_key_ok(websocket, settings):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    try:
        import websockets
    except ImportError as exc:
        logger.error("websockets package missing: %s", exc)
        await websocket.send_json(
            {"type": "error", "message": "Server missing websockets dependency"}
        )
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    upstream_url = settings.engine_job_logs_ws_url(job_id)
    extra_headers = {}
    if settings.engine_api_key:
        extra_headers["X-API-Key"] = settings.engine_api_key

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers=extra_headers,
            open_timeout=settings.engine_timeout_seconds,
        ) as upstream:
            async def client_to_upstream() -> None:
                try:
                    while True:
                        # Drain client pings/noise; engine does not need them.
                        await websocket.receive_text()
                except WebSocketDisconnect:
                    return

            async def upstream_to_client() -> None:
                try:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception:
                    return

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                # Surface task exceptions in logs.
                try:
                    task.result()
                except Exception:
                    logger.debug("WS proxy task ended job_id=%s", job_id, exc_info=True)
    except LookupError:
        await websocket.send_json({"type": "error", "message": "Job not found", "jobId": job_id})
    except Exception as exc:
        logger.exception("Job logs WS proxy failed job_id=%s", job_id)
        try:
            await websocket.send_json(
                {"type": "error", "message": str(exc), "jobId": job_id}
            )
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
