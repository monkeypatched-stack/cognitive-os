from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

BookingStatus = Literal["booked", "cancelled"]


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CalendarBooking(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    calendar_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=160)
    start_at: datetime
    end_at: datetime
    booked_by: Optional[str] = None
    attendee_ids: list[str] = Field(default_factory=list)
    description: Optional[str] = Field(None, max_length=1000)
    status: BookingStatus = "booked"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "CalendarBooking":
        self.start_at = ensure_utc(self.start_at)
        self.end_at = ensure_utc(self.end_at)
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class CalendarBookingCreate(CalendarBooking):
    pass


class CalendarBookingUpdate(BaseModel):
    calendar_id: Optional[str] = Field(None, min_length=1)
    title: Optional[str] = Field(None, min_length=1, max_length=160)
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    booked_by: Optional[str] = None
    attendee_ids: Optional[list[str]] = None
    description: Optional[str] = Field(None, max_length=1000)
    status: Optional[BookingStatus] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "CalendarBookingUpdate":
        self.start_at = ensure_utc(self.start_at)
        self.end_at = ensure_utc(self.end_at)
        self.updated_at = ensure_utc(self.updated_at)
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.end_at <= self.start_at
        ):
            raise ValueError("end_at must be after start_at")
        return self


class CalendarBookingResponse(CalendarBooking):
    class Config:
        from_attributes = True


class PaginatedCalendarBookingResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[CalendarBookingResponse]
