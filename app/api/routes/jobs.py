from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from secrets import compare_digest
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.db.session import SessionLocal, get_db
from app.schemas.request import EngineJobStatusResponse
from app.services.engine_client import get_job, get_job_logs
from app.services.runs_store import get_run_by_job_ref
from app.services.submit_job import (
    STATUS_BUILDING,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUBMITTED,
)

logger = logging.getLogger("hhs.jobs")

router = APIRouter(prefix="/api-data/v1/jobs", tags=["jobs"])


def _local_status_payload(run: dict[str, Any], job_ref: str) -> dict[str, Any]:
    """Map hhs_engine_runs row to Panel-facing job status before/without engine."""
    status_raw = (run.get("status") or STATUS_PENDING).upper()
    job_type = run.get("job_type")
    created = run.get("created_at")
    updated = run.get("updated_at") or created

    if status_raw == STATUS_FAILED:
        return {
            "job_id": str(run.get("token") or job_ref),
            "job_type": job_type,
            "status": "failed",
            "result": None,
            "error": run.get("error") or "Job failed before engine submit",
            "created_at": created,
            "started_at": updated,
            "completed_at": updated,
            "progress_percent": 0,
            "progress_message": "failed",
        }
    if status_raw == STATUS_BUILDING:
        return {
            "job_id": str(run.get("token") or job_ref),
            "job_type": job_type,
            "status": "running",
            "result": None,
            "error": None,
            "created_at": created,
            "started_at": updated,
            "completed_at": None,
            "progress_percent": 5,
            "progress_message": "building payload",
        }
    if status_raw == STATUS_SUBMITTED and not run.get("engine_job_id"):
        return {
            "job_id": str(run.get("token") or job_ref),
            "job_type": job_type,
            "status": "queued",
            "result": None,
            "error": None,
            "created_at": created,
            "started_at": updated,
            "completed_at": None,
            "progress_percent": 10,
            "progress_message": "submitted",
        }
    # PENDING (and any unknown pre-engine state)
    return {
        "job_id": str(run.get("token") or job_ref),
        "job_type": job_type,
        "status": "queued",
        "result": None,
        "error": None,
        "created_at": created,
        "started_at": None,
        "completed_at": None,
        "progress_percent": 0,
        "progress_message": "queued",
    }


def _to_response(body: dict[str, Any], fallback_job_id: str) -> EngineJobStatusResponse:
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
        job_id=str(body.get("job_id") or fallback_job_id),
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rewrite_upstream_frame(raw: str | bytes, bridge_token: str) -> str:
    """Rewrite engine jobId → bridge token so Panel always sees the stable id."""
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return text
    if isinstance(payload, dict) and "jobId" in payload:
        payload["jobId"] = bridge_token
        return json.dumps(payload)
    return text


@router.get(
    "/{job_id}",
    response_model=EngineJobStatusResponse,
    dependencies=[Depends(require_api_key)],
)
async def get_engine_job(
    job_id: str,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> EngineJobStatusResponse:
    """
    Job status for Panel polling.

    `job_id` is the stable bridge token from the 202 accept response.
    Until the engine accepts the job, status is served from hhs_engine_runs;
    afterward it proxies engine-service (rewriting job_id back to the bridge token).
    """
    run = get_run_by_job_ref(db, job_id)
    if run is None:
        # Legacy: direct engine id with no local run row
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
        return _to_response(body, job_id)

    engine_job_id = run.get("engine_job_id")
    status_raw = (run.get("status") or "").upper()

    if status_raw == STATUS_FAILED or not engine_job_id:
        return _to_response(_local_status_payload(run, job_id), job_id)

    try:
        body = await get_job(settings, str(engine_job_id))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Job status proxy failed job_id=%s engine=%s", job_id, engine_job_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    # Keep Panel's poll token stable
    body = {**body, "job_id": str(run.get("token") or job_id)}
    if not body.get("job_type") and run.get("job_type"):
        body["job_type"] = run["job_type"]
    return _to_response(body, job_id)


@router.get(
    "/{job_id}/logs",
    dependencies=[Depends(require_api_key)],
)
async def get_engine_job_logs(
    job_id: str,
    after: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> dict:
    """Proxy engine-service job log backlog (or local bridge progress before submit)."""
    run = get_run_by_job_ref(db, job_id)
    if run is not None and not run.get("engine_job_id"):
        status_raw = (run.get("status") or STATUS_PENDING).upper()
        if status_raw == STATUS_FAILED:
            line = f"Bridge job failed: {run.get('error') or 'unknown error'}"
        elif status_raw == STATUS_BUILDING:
            line = "Building engine payload from roster visits…"
        else:
            line = "Job queued; waiting to build payload…"
        seq = 1
        if after >= seq:
            return {"lines": [], "nextSeq": after}
        return {
            "lines": [{"seq": seq, "line": line, "ts": run.get("updated_at") or run.get("created_at")}],
            "nextSeq": seq,
        }

    upstream_id = str((run or {}).get("engine_job_id") or job_id)
    try:
        return await get_job_logs(settings, upstream_id, after=after)
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
    """
    Proxy live engine-service job log WebSocket to Panel clients.

    Waits until the bridge has an engine_job_id (background submit finished),
    emitting status + log frames so loading UI keeps working across Panel ↔ bridge ↔ engine.
    """
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

    engine_job_id: str | None = None
    bridge_token = job_id
    bridge_log_seq = 0
    last_status_msg: str | None = None

    async def _emit_bridge_progress(run: dict[str, Any], status_raw: str) -> None:
        nonlocal bridge_log_seq, last_status_msg
        if status_raw == STATUS_BUILDING:
            msg = "Building engine payload from roster visits…"
            pct = 5
            st = "running"
        else:
            msg = "Job queued; waiting to build payload…"
            pct = 0
            st = "queued"
        await websocket.send_json(
            {
                "type": "status",
                "jobId": bridge_token,
                "status": st,
                "progressPercent": pct,
                "progressMessage": msg if status_raw == STATUS_BUILDING else "queued",
            }
        )
        if msg != last_status_msg:
            last_status_msg = msg
            bridge_log_seq += 1
            await websocket.send_json(
                {
                    "type": "log",
                    "jobId": bridge_token,
                    "seq": bridge_log_seq,
                    "line": msg,
                    "ts": run.get("updated_at") or run.get("created_at") or _now_iso(),
                }
            )

    for _ in range(180):  # ~3 minutes for large payload builds
        db = SessionLocal()
        try:
            run = get_run_by_job_ref(db, job_id)
        finally:
            db.close()

        if run is None:
            engine_job_id = job_id
            break

        bridge_token = str(run.get("token") or job_id)
        status_raw = (run.get("status") or "").upper()
        if status_raw == STATUS_FAILED:
            err = run.get("error") or "failed"
            await websocket.send_json(
                {
                    "type": "status",
                    "jobId": bridge_token,
                    "status": "failed",
                    "progressPercent": 0,
                    "progressMessage": err,
                }
            )
            bridge_log_seq += 1
            await websocket.send_json(
                {
                    "type": "log",
                    "jobId": bridge_token,
                    "seq": bridge_log_seq,
                    "line": f"Bridge job failed: {err}",
                    "ts": _now_iso(),
                }
            )
            await websocket.send_json(
                {"type": "done", "jobId": bridge_token, "status": "failed"}
            )
            await websocket.close()
            return

        if run.get("engine_job_id"):
            engine_job_id = str(run["engine_job_id"])
            await websocket.send_json(
                {
                    "type": "status",
                    "jobId": bridge_token,
                    "status": "running",
                    "progressPercent": 10,
                    "progressMessage": "submitted to engine",
                }
            )
            bridge_log_seq += 1
            await websocket.send_json(
                {
                    "type": "log",
                    "jobId": bridge_token,
                    "seq": bridge_log_seq,
                    "line": "Submitted to engine-service; streaming solver logs…",
                    "ts": _now_iso(),
                }
            )
            break

        await _emit_bridge_progress(run, status_raw)
        await asyncio.sleep(1.0)
    else:
        await websocket.send_json(
            {
                "type": "error",
                "message": "Timed out waiting for engine submit",
                "jobId": bridge_token,
            }
        )
        await websocket.close()
        return

    assert engine_job_id is not None
    upstream_url = settings.engine_job_logs_ws_url(engine_job_id)
    extra_headers = {}
    if settings.engine_api_key:
        extra_headers["X-API-Key"] = settings.engine_api_key

    logger.info(
        "Proxying job logs WS bridge_token=%s engine_job_id=%s url=%s",
        bridge_token,
        engine_job_id,
        upstream_url,
    )

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers=extra_headers,
            open_timeout=settings.engine_timeout_seconds,
        ) as upstream:
            async def client_to_upstream() -> None:
                try:
                    while True:
                        await websocket.receive_text()
                except WebSocketDisconnect:
                    return

            async def upstream_to_client() -> None:
                try:
                    async for message in upstream:
                        frame = _rewrite_upstream_frame(message, bridge_token)
                        await websocket.send_text(frame)
                except Exception:
                    logger.exception(
                        "Upstream log WS ended bridge_token=%s engine=%s",
                        bridge_token,
                        engine_job_id,
                    )
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
                try:
                    task.result()
                except Exception:
                    logger.debug("WS proxy task ended job_id=%s", job_id, exc_info=True)
    except LookupError:
        await websocket.send_json({"type": "error", "message": "Job not found", "jobId": bridge_token})
    except Exception as exc:
        logger.exception("Job logs WS proxy failed job_id=%s", job_id)
        try:
            await websocket.send_json(
                {"type": "error", "message": str(exc), "jobId": bridge_token}
            )
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
