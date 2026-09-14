from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, model_validator


class ChangeoverWindow(BaseModel):
    """Reserved schedule block for a changeover."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    workstation_id: str
    factory_id: str
    event_id: Optional[str] = Field(
        None, description="Linked ChangeoverEvent once created"
    )
    calendar_booking_id: Optional[str] = Field(
        None, description="Linked calendar booking once added to calendar"
    )
    start_dt: datetime
    end_dt: datetime
    buffer_minutes: int = Field(
        default=0, ge=0, le=120, description="Safety buffer after planned end"
    )
    locked: bool = Field(
        default=False, description="Locked windows cannot be moved by auto-scheduler"
    )

    @model_validator(mode="after")
    def end_after_start(self):
        if self.end_dt <= self.start_dt:
            raise ValueError("end_dt must be after start_dt")
        return self

    @computed_field  # type: ignore[misc]
    @property
    def total_blocked_minutes(self) -> float:
        window = (self.end_dt - self.start_dt).total_seconds() / 60
        return window + self.buffer_minutes

    @computed_field  # type: ignore[misc]
    @property
    def earliest_production_resume(self) -> datetime:
        return self.end_dt + timedelta(minutes=self.buffer_minutes)


class ChangeoverWindowCreate(ChangeoverWindow):
    pass


class ChangeoverWindowUpdate(BaseModel):
    workstation_id: Optional[str] = None
    factory_id: Optional[str] = None
    event_id: Optional[str] = None
    calendar_booking_id: Optional[str] = None
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    buffer_minutes: Optional[int] = Field(None, ge=0, le=120)
    locked: Optional[bool] = None


class ChangeoverWindowCalendarBookingCreate(BaseModel):
    calendar_id: Optional[str] = Field(None, min_length=1)
    title: Optional[str] = Field(None, min_length=1, max_length=160)
    booked_by: Optional[str] = None
    attendee_ids: list[str] = Field(default_factory=list)
    description: Optional[str] = Field(None, max_length=1000)
    include_buffer: bool = True


class ChangeoverWindowResponse(ChangeoverWindow):
    class Config:
        from_attributes = True


class PaginatedChangeoverWindowResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ChangeoverWindowResponse]
