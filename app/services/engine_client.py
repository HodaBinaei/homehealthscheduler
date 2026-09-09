from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("hhs.engine_client")


def _payload_counts(payload: dict[str, Any]) -> tuple[int, int, int, str | None]:
    data = payload.get("data") or {}
    caregivers = data.get("caregivers") or {}
    patients = data.get("patient") or {}
    feasible = data.get("crid_prid_feasible") or []
    return len(caregivers), len(patients), len(feasible), data.get("date_data")


def _log_outbound_payload(settings: Settings, url: str, payload: dict[str, Any], body: str) -> None:
    caregiver_count, patient_count, feasible_count, date_data = _payload_counts(payload)
    logger.info(
        "Submitting engine execute to %s (%s bytes, %s caregivers, %s patients, %s feasible pairs)",
        url,
        len(body),
        caregiver_count,
        patient_count,
        feasible_count,
    )
    if settings.log_payload:
        logger.info("Engine execute payload:\n%s", json.dumps(payload, indent=2, default=str))
    else:
        logger.info(
            "Engine execute payload summary: date=%s caregivers=%s patients=%s feasible=%s",
            date_data,
            caregiver_count,
            patient_count,
            feasible_count,
        )


def _parse_engine_response(response: httpx.Response) -> str | None:
    try:
        response_body = response.json()
    except ValueError:
        response_body = {"raw": response.text}

    if response.status_code >= 400:
        detail = json.dumps(response_body) if isinstance(response_body, dict) else str(response_body)
        logger.error("Engine execute failed (%s): %s", response.status_code, detail)
        raise RuntimeError(f"Engine execute failed ({response.status_code}): {detail}")

    token = response_body.get("token") if isinstance(response_body, dict) else None
    logger.info("Engine execute accepted; token=%s", token)
    return token


def submit_execute_sync(settings: Settings, payload: dict[str, Any]) -> str | None:
    """Synchronous POST to ENGINE_URL (for background tasks)."""
    url = settings.engine_url
    body = json.dumps(payload, default=str)
    _log_outbound_payload(settings, url, payload, body)
    try:
        with httpx.Client(timeout=settings.engine_timeout_seconds) as client:
            response = client.post(
                url,
                content=body,
                headers={"Content-Type": "application/json"},
            )
    except httpx.HTTPError as exc:
        logger.error("Engine execute request failed: %s", exc)
        raise ConnectionError(f"Engine execute request failed: {exc}") from exc
    return _parse_engine_response(response)


async def submit_execute(
    settings: Settings,
    payload: dict[str, Any],
) -> str | None:
    """POST payload to ENGINE_URL. Returns engine token if present."""
    url = settings.engine_url
    body = json.dumps(payload, default=str)
    _log_outbound_payload(settings, url, payload, body)

    try:
        async with httpx.AsyncClient(timeout=settings.engine_timeout_seconds) as client:
            response = await client.post(
                url,
                content=body,
                headers={"Content-Type": "application/json"},
            )
    except httpx.HTTPError as exc:
        logger.error("Engine execute request failed: %s", exc)
        raise ConnectionError(f"Engine execute request failed: {exc}") from exc

    return _parse_engine_response(response)
