from datetime import date

from pydantic import BaseModel, Field


class ScheduleExecuteRequest(BaseModel):
    date: date
    hour: int = Field(..., ge=0, le=23, description="Accepted for later use; ignored in v1")


class ScheduleExecuteResponse(BaseModel):
    status: str = "accepted"
    date: str
