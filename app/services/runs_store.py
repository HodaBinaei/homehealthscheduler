from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import engine

SELECT_COLS = """
    id, token, roster_date, hour, status, error,
    caregiver_count, patient_count, feasible_count,
    request_payload_s3_key, created_at
"""


def ensure_runs_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS hhs_engine_runs (
                    id UUID PRIMARY KEY,
                    token VARCHAR(255),
                    roster_date DATE NOT NULL,
                    hour INTEGER,
                    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
                    error TEXT,
                    caregiver_count INTEGER,
                    patient_count INTEGER,
                    feasible_count INTEGER,
                    request_payload_s3_key VARCHAR(1024),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        # Backward-compatible add for existing installs
        conn.execute(
            text(
                """
                ALTER TABLE hhs_engine_runs
                ADD COLUMN IF NOT EXISTS request_payload_s3_key VARCHAR(1024)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_hhs_engine_runs_created_at
                    ON hhs_engine_runs (created_at DESC)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_hhs_engine_runs_roster_date
                    ON hhs_engine_runs (roster_date)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_hhs_engine_runs_token
                    ON hhs_engine_runs (token)
                """
            )
        )


def insert_run(
    db: Session,
    *,
    roster_date: date,
    hour: int | None,
    token: str | None,
    status: str,
    error: str | None = None,
    caregiver_count: int | None = None,
    patient_count: int | None = None,
    feasible_count: int | None = None,
    request_payload_s3_key: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    run_id = run_id or str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO hhs_engine_runs (
                id, token, roster_date, hour, status, error,
                caregiver_count, patient_count, feasible_count,
                request_payload_s3_key
            ) VALUES (
                CAST(:id AS uuid), :token, :roster_date, :hour, :status, :error,
                :caregiver_count, :patient_count, :feasible_count,
                :request_payload_s3_key
            )
            """
        ),
        {
            "id": run_id,
            "token": token,
            "roster_date": roster_date.isoformat(),
            "hour": hour,
            "status": status,
            "error": error,
            "caregiver_count": caregiver_count,
            "patient_count": patient_count,
            "feasible_count": feasible_count,
            "request_payload_s3_key": request_payload_s3_key,
        },
    )
    db.commit()
    return get_run_by_id(db, run_id) or {
        "id": run_id,
        "token": token,
        "roster_date": roster_date.isoformat(),
        "hour": hour,
        "status": status,
        "error": error,
        "caregiver_count": caregiver_count,
        "patient_count": patient_count,
        "feasible_count": feasible_count,
        "request_payload_s3_key": request_payload_s3_key,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    roster_date = data.get("roster_date")
    if hasattr(roster_date, "isoformat"):
        data["roster_date"] = roster_date.isoformat()
    created_at = data.get("created_at")
    if hasattr(created_at, "isoformat"):
        data["created_at"] = created_at.isoformat()
    if data.get("id") is not None:
        data["id"] = str(data["id"])
    return data


def get_run_by_id(db: Session, run_id: str) -> dict[str, Any] | None:
    row = db.execute(
        text(
            f"""
            SELECT {SELECT_COLS}
            FROM hhs_engine_runs
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": run_id},
    ).mappings().first()
    return _row_to_dict(row) if row else None


def get_run_by_token(db: Session, token: str) -> dict[str, Any] | None:
    row = db.execute(
        text(
            f"""
            SELECT {SELECT_COLS}
            FROM hhs_engine_runs
            WHERE token = :token
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"token": token},
    ).mappings().first()
    return _row_to_dict(row) if row else None


def list_runs(
    db: Session,
    *,
    roster_date: date | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    if roster_date is not None:
        rows = db.execute(
            text(
                f"""
                SELECT {SELECT_COLS}
                FROM hhs_engine_runs
                WHERE roster_date = :roster_date
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"roster_date": roster_date.isoformat(), "limit": limit},
        ).mappings().all()
    else:
        rows = db.execute(
            text(
                f"""
                SELECT {SELECT_COLS}
                FROM hhs_engine_runs
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()
    return [_row_to_dict(row) for row in rows]
