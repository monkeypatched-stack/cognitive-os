from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Worker(BaseModel):
    """Represents a worker in the manufacturing facility."""

    worker_id: str = Field(
        ..., min_length=1, description="Unique identifier for the worker"
    )
    name: str = Field(..., min_length=1, description="Full name of the worker")
    role: str = Field(..., min_length=1, description="Job role or position")
    worker_type: str = Field(
        ...,
        description="Type of worker (Plant Worker, Line Worker, Workstation Worker)",
    )

    # Location/assignment fields
    plant_id: Optional[str] = Field(
        None, description="ID of the plant where worker is assigned"
    )
    line_id: Optional[str] = Field(
        None, description="ID of the production line where worker is assigned"
    )
    workstation_id: Optional[str] = Field(
        None, description="ID of the specific workstation where worker is assigned"
    )

    # Hierarchy/management fields
    reports_to_worker_id: Optional[str] = Field(
        None, description="ID of the worker this worker reports to"
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=utc_now, description="When the worker record was created"
    )
    updated_at: datetime = Field(
        default_factory=utc_now, description="When the worker record was last updated"
    )

    # Device assignment (new field)
    device_id: Optional[str] = Field(
        None, description="ID of the device assigned to this worker"
    )

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "Worker":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class WorkerCreate(Worker):
    """Model for creating a new worker."""

    pass


class WorkerUpdate(BaseModel):
    """Model for updating worker information."""

    name: Optional[str] = Field(None, min_length=1)
    role: Optional[str] = Field(None, min_length=1)
    worker_type: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    workstation_id: Optional[str] = None
    reports_to_worker_id: Optional[str] = None
    device_id: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_update_datetimes(self) -> "WorkerUpdate":
        self.updated_at = ensure_utc(self.updated_at)
        return self


class WorkerResponse(Worker):
    """Response model for worker data."""

    class Config:
        from_attributes = True
