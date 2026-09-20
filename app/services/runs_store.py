from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import engine

SELECT_COLS = """
    id, token, roster_date, hour, status, error, job_type,
    engine_job_id, caregiver_count, patient_count, feasible_count,
    request_payload_s3_key, created_at, updated_at
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
        for stmt in (
            """
            ALTER TABLE hhs_engine_runs
            ADD COLUMN IF NOT EXISTS request_payload_s3_key VARCHAR(1024)
            """,
            """
            ALTER TABLE hhs_engine_runs
            ADD COLUMN IF NOT EXISTS job_type VARCHAR(64)
            """,
            """
            ALTER TABLE hhs_engine_runs
            ADD COLUMN IF NOT EXISTS engine_job_id VARCHAR(255)
            """,
            """
            ALTER TABLE hhs_engine_runs
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_hhs_engine_runs_created_at
                ON hhs_engine_runs (created_at DESC)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_hhs_engine_runs_roster_date
                ON hhs_engine_runs (roster_date)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_hhs_engine_runs_token
                ON hhs_engine_runs (token)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_hhs_engine_runs_engine_job_id
                ON hhs_engine_runs (engine_job_id)
            """,
        ):
            conn.execute(text(stmt))


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
    job_type: str | None = None,
    engine_job_id: str | None = None,
) -> dict[str, Any]:
    run_id = run_id or str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO hhs_engine_runs (
                id, token, roster_date, hour, status, error, job_type,
                engine_job_id, caregiver_count, patient_count, feasible_count,
                request_payload_s3_key, updated_at
            ) VALUES (
                CAST(:id AS uuid), :token, :roster_date, :hour, :status, :error, :job_type,
                :engine_job_id, :caregiver_count, :patient_count, :feasible_count,
                :request_payload_s3_key, NOW()
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
            "job_type": job_type,
            "engine_job_id": engine_job_id,
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
        "job_type": job_type,
        "engine_job_id": engine_job_id,
        "caregiver_count": caregiver_count,
        "patient_count": patient_count,
        "feasible_count": feasible_count,
        "request_payload_s3_key": request_payload_s3_key,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


def update_run(
    db: Session,
    run_id: str,
    *,
    status: str | None = None,
    error: str | None = None,
    clear_error: bool = False,
    token: str | None = None,
    engine_job_id: str | None = None,
    job_type: str | None = None,
    caregiver_count: int | None = None,
    patient_count: int | None = None,
    feasible_count: int | None = None,
    request_payload_s3_key: str | None = None,
) -> dict[str, Any] | None:
    fields: list[str] = ["updated_at = NOW()"]
    params: dict[str, Any] = {"id": run_id}
    if status is not None:
        fields.append("status = :status")
        params["status"] = status
    if clear_error:
        fields.append("error = NULL")
    elif error is not None:
        fields.append("error = :error")
        params["error"] = error
    if token is not None:
        fields.append("token = :token")
        params["token"] = token
    if engine_job_id is not None:
        fields.append("engine_job_id = :engine_job_id")
        params["engine_job_id"] = engine_job_id
    if job_type is not None:
        fields.append("job_type = :job_type")
        params["job_type"] = job_type
    if caregiver_count is not None:
        fields.append("caregiver_count = :caregiver_count")
        params["caregiver_count"] = caregiver_count
    if patient_count is not None:
        fields.append("patient_count = :patient_count")
        params["patient_count"] = patient_count
    if feasible_count is not None:
        fields.append("feasible_count = :feasible_count")
        params["feasible_count"] = feasible_count
    if request_payload_s3_key is not None:
        fields.append("request_payload_s3_key = :request_payload_s3_key")
        params["request_payload_s3_key"] = request_payload_s3_key

    db.execute(
        text(f"UPDATE hhs_engine_runs SET {', '.join(fields)} WHERE id = CAST(:id AS uuid)"),
        params,
    )
    db.commit()
    return get_run_by_id(db, run_id)


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    roster_date = data.get("roster_date")
    if hasattr(roster_date, "isoformat"):
        data["roster_date"] = roster_date.isoformat()
    for key in ("created_at", "updated_at"):
        value = data.get(key)
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
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


def get_run_by_job_ref(db: Session, job_ref: str) -> dict[str, Any] | None:
    """Resolve Panel poll token: bridge job_id (token), run id, or engine_job_id."""
    by_token = get_run_by_token(db, job_ref)
    if by_token:
        return by_token
    try:
        by_id = get_run_by_id(db, job_ref)
        if by_id:
            return by_id
    except Exception:
        pass
    row = db.execute(
        text(
            f"""
            SELECT {SELECT_COLS}
            FROM hhs_engine_runs
            WHERE engine_job_id = :engine_job_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"engine_job_id": job_ref},
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
