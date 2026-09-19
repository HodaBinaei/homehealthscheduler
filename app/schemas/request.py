from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class ScheduleExecuteRequest(BaseModel):
    date: date
    hour: int = Field(..., ge=0, le=23, description="Accepted and stored on the run; unused by payload build in v1")


class ScheduleJobResponse(BaseModel):
    status: str = "accepted"
    date: str
    job_id: str
    job_type: str
    run_id: str | None = None


# Backward-compatible alias
ScheduleExecuteResponse = ScheduleJobResponse


class EngineRunItem(BaseModel):
    id: str
    token: str | None = None
    roster_date: str
    hour: int | None = None
    status: str
    error: str | None = None
    caregiver_count: int | None = None
    patient_count: int | None = None
    feasible_count: int | None = None
    request_payload_s3_key: str | None = None
    created_at: datetime | str


class EngineRunsResponse(BaseModel):
    runs: list[EngineRunItem]
    count: int


class PayloadDownloadResponse(BaseModel):
    id: str
    token: str | None = None
    roster_date: str
    request_payload_s3_key: str
    download_url: str
    expires_in_seconds: int


class EngineJobStatusResponse(BaseModel):
    job_id: str
    job_type: str | None = None
    status: str | None = None
    result: Any = None
    error: Any = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
