from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("hhs.engine_client")

JobType = str  # "full-assignment" | "multicpsat" | "reschedule"


def _engine_headers(settings: Settings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.engine_api_key:
        headers["X-API-Key"] = settings.engine_api_key
    return headers


def _log_outbound(settings: Settings, url: str, job_type: str, payload: dict[str, Any], body: str) -> None:
    caregiver_count = len(payload.get("caregiver_dict") or [])
    patient_count = len(payload.get("patient_dict") or [])
    feasible_count = len(payload.get("crid_prid_feasible_dict") or [])
    logger.info(
        "Submitting engine %s to %s (%s bytes, %s caregivers, %s patients, %s feasible pairs)",
        job_type,
        url,
        len(body),
        caregiver_count,
        patient_count,
        feasible_count,
    )
    if settings.log_payload:
        logger.info("Engine %s payload:\n%s", job_type, json.dumps(payload, indent=2, default=str))
    else:
        logger.info(
            "Engine %s payload summary: caregivers=%s patients=%s feasible=%s",
            job_type,
            caregiver_count,
            patient_count,
            feasible_count,
        )


def _parse_job_accepted(response: httpx.Response, job_type: str) -> dict[str, Any]:
    try:
        response_body = response.json()
    except ValueError:
        response_body = {"raw": response.text}

    if response.status_code >= 400:
        detail = json.dumps(response_body) if isinstance(response_body, dict) else str(response_body)
        logger.error("Engine %s failed (%s): %s", job_type, response.status_code, detail)
        raise RuntimeError(f"Engine {job_type} failed ({response.status_code}): {detail}")

    if not isinstance(response_body, dict) or not response_body.get("job_id"):
        raise RuntimeError(f"Engine {job_type} response missing job_id: {response_body}")

    logger.info(
        "Engine %s accepted; job_id=%s status=%s",
        job_type,
        response_body.get("job_id"),
        response_body.get("status"),
    )
    return {
        "job_id": str(response_body["job_id"]),
        "job_type": str(response_body.get("job_type") or job_type),
        "status": str(response_body.get("status") or "queued"),
    }


def _url_for_job_type(settings: Settings, job_type: JobType) -> str:
    if job_type == "full-assignment":
        return settings.engine_full_assignment_url
    if job_type == "multicpsat":
        return settings.engine_multicpsat_url
    if job_type == "reschedule":
        return settings.engine_reschedule_url
    raise ValueError(f"Unknown engine job type: {job_type}")


def submit_scheduler_job_sync(
    settings: Settings,
    job_type: JobType,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Synchronous POST to an engine-service scheduler endpoint."""
    from app.services.payload.travel_bounds import scrub_engine_distance_matrices

    payload = scrub_engine_distance_matrices(dict(payload))
    url = _url_for_job_type(settings, job_type)
    body = json.dumps(payload, default=str)
    _log_outbound(settings, url, job_type, payload, body)
    try:
        with httpx.Client(timeout=settings.engine_timeout_seconds) as client:
            response = client.post(url, content=body, headers=_engine_headers(settings))
    except httpx.HTTPError as exc:
        logger.error("Engine %s request failed: %s", job_type, exc)
        raise ConnectionError(f"Engine {job_type} request failed: {exc}") from exc
    return _parse_job_accepted(response, job_type)


async def submit_scheduler_job(
    settings: Settings,
    job_type: JobType,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Async POST to an engine-service scheduler endpoint."""
    from app.services.payload.travel_bounds import scrub_engine_distance_matrices

    payload = scrub_engine_distance_matrices(dict(payload))
    url = _url_for_job_type(settings, job_type)
    body = json.dumps(payload, default=str)
    _log_outbound(settings, url, job_type, payload, body)
    try:
        async with httpx.AsyncClient(timeout=settings.engine_timeout_seconds) as client:
            response = await client.post(url, content=body, headers=_engine_headers(settings))
    except httpx.HTTPError as exc:
        logger.error("Engine %s request failed: %s", job_type, exc)
        raise ConnectionError(f"Engine {job_type} request failed: {exc}") from exc
    return _parse_job_accepted(response, job_type)


def get_job_sync(settings: Settings, job_id: str) -> dict[str, Any]:
    """Synchronous GET of engine-service job status."""
    url = settings.engine_job_url(job_id)
    try:
        with httpx.Client(timeout=settings.engine_timeout_seconds) as client:
            response = client.get(url, headers=_engine_headers(settings))
    except httpx.HTTPError as exc:
        logger.error("Engine get_job failed: %s", exc)
        raise ConnectionError(f"Engine get_job request failed: {exc}") from exc

    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}

    if response.status_code == 404:
        raise LookupError("Job not found")
    if response.status_code >= 400:
        detail = json.dumps(body) if isinstance(body, dict) else str(body)
        raise RuntimeError(f"Engine get_job failed ({response.status_code}): {detail}")
    if not isinstance(body, dict):
        raise RuntimeError(f"Engine get_job returned non-object: {body}")
    return body


async def get_job(settings: Settings, job_id: str) -> dict[str, Any]:
    """Async GET of engine-service job status."""
    url = settings.engine_job_url(job_id)
    try:
        async with httpx.AsyncClient(timeout=settings.engine_timeout_seconds) as client:
            response = await client.get(url, headers=_engine_headers(settings))
    except httpx.HTTPError as exc:
        logger.error("Engine get_job failed: %s", exc)
        raise ConnectionError(f"Engine get_job request failed: {exc}") from exc

    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}

    if response.status_code == 404:
        raise LookupError("Job not found")
    if response.status_code >= 400:
        detail = json.dumps(body) if isinstance(body, dict) else str(body)
        raise RuntimeError(f"Engine get_job failed ({response.status_code}): {detail}")
    if not isinstance(body, dict):
        raise RuntimeError(f"Engine get_job returned non-object: {body}")
    return body


async def get_job_logs(settings: Settings, job_id: str, after: int = 0) -> dict[str, Any]:
    """Async GET of engine-service job log backlog."""
    url = f"{settings.engine_job_logs_url(job_id)}?after={int(after)}"
    try:
        async with httpx.AsyncClient(timeout=settings.engine_timeout_seconds) as client:
            response = await client.get(url, headers=_engine_headers(settings))
    except httpx.HTTPError as exc:
        logger.error("Engine get_job_logs failed: %s", exc)
        raise ConnectionError(f"Engine get_job_logs request failed: {exc}") from exc

    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}

    if response.status_code == 404:
        raise LookupError("Job not found")
    if response.status_code >= 400:
        detail = json.dumps(body) if isinstance(body, dict) else str(body)
        raise RuntimeError(f"Engine get_job_logs failed ({response.status_code}): {detail}")
    if not isinstance(body, dict):
        raise RuntimeError(f"Engine get_job_logs returned non-object: {body}")
    return body


async def cancel_job(settings: Settings, job_id: str) -> dict[str, Any]:
    """Async POST to cancel an engine-service job."""
    url = settings.engine_job_cancel_url(job_id)
    try:
        async with httpx.AsyncClient(timeout=settings.engine_timeout_seconds) as client:
            response = await client.post(url, headers=_engine_headers(settings))
    except httpx.HTTPError as exc:
        logger.error("Engine cancel_job failed: %s", exc)
        raise ConnectionError(f"Engine cancel_job request failed: {exc}") from exc

    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}

    if response.status_code == 404:
        raise LookupError("Job not found")
    if response.status_code >= 400:
        detail = json.dumps(body) if isinstance(body, dict) else str(body)
        raise RuntimeError(f"Engine cancel_job failed ({response.status_code}): {detail}")
    if not isinstance(body, dict):
        raise RuntimeError(f"Engine cancel_job returned non-object: {body}")
    return body


# Convenience wrappers
def submit_full_assignment_sync(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    return submit_scheduler_job_sync(settings, "full-assignment", payload)


def submit_multicpsat_sync(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    return submit_scheduler_job_sync(settings, "multicpsat", payload)


def submit_reschedule_sync(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    return submit_scheduler_job_sync(settings, "reschedule", payload)
