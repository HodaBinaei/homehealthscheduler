from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ScheduleExecuteRequest(BaseModel):
    date: date
    hour: int = Field(
        ...,
        ge=0,
        le=23,
        description="Accepted and stored on the run; unused by payload build in v1",
    )


class MultiScheduleExecuteRequest(ScheduleExecuteRequest):
    """Subset multicpsat: only the selected visits and caregivers are processed."""

    visitIds: list[UUID] = Field(
        ...,
        min_length=1,
        description="Roster visit UUIDs for this date (patients/calls to include)",
    )
    providerUserIds: list[int] = Field(
        ...,
        min_length=1,
        description="Panel caregiver user ids (same as suggest providerUserIds)",
    )

    @field_validator("visitIds")
    @classmethod
    def _non_empty_visit_ids(cls, value: list[UUID]) -> list[UUID]:
        if not value:
            raise ValueError("must be a non-empty list")
        return value

    @field_validator("providerUserIds")
    @classmethod
    def _non_empty_provider_ids(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("must be a non-empty list")
        cleaned = [int(v) for v in value]
        if any(v <= 0 for v in cleaned):
            raise ValueError("ids must be positive integers")
        return cleaned


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
    progress_percent: int | None = None
    progress_message: str | None = None
