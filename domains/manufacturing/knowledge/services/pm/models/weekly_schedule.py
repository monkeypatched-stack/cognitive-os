# ---------------------------------------------------------------------------
# Weekly Schedule  (aggregates ShiftSchedules for a full week)
# ---------------------------------------------------------------------------

from datetime import date, datetime, timedelta
from typing import Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from services.shifts.models.shift import ShiftSchedule


class WeeklySchedule(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    factory_id: UUID
    week_start: date = Field(..., description="Must be a Monday")
    shifts: list[ShiftSchedule] = Field(default_factory=list)
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    notes: Optional[str] = Field(None, max_length=1000)

    @field_validator("week_start")
    @classmethod
    def must_be_monday(cls, v: date) -> date:
        if v.weekday() != 0:
            raise ValueError(
                f"week_start must be a Monday; got {v.strftime('%A')} ({v})"
            )
        return v

    @computed_field  # type: ignore[misc]
    @property
    def week_end(self) -> date:
        return self.week_start + timedelta(days=6)

    @computed_field  # type: ignore[misc]
    @property
    def total_shifts(self) -> int:
        return len(self.shifts)

    @computed_field  # type: ignore[misc]
    @property
    def total_headcount(self) -> int:
        return sum(s.headcount for s in self.shifts)

    @model_validator(mode="after")
    def shifts_within_week(self):
        for shift in self.shifts:
            if not (self.week_start <= shift.shift_date <= self.week_end):
                raise ValueError(
                    f"Shift {shift.id} date {shift.shift_date} falls outside "
                    f"the week {self.week_start} - {self.week_end}"
                )
        return self


class WeeklyScheduleCreate(WeeklySchedule):
    pass


class WeeklyScheduleUpdate(BaseModel):
    factory_id: Optional[UUID] = None
    week_start: Optional[date] = None
    shifts: Optional[list[ShiftSchedule]] = None
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    notes: Optional[str] = Field(None, max_length=1000)

    @field_validator("week_start")
    @classmethod
    def must_be_monday(cls, v: Optional[date]) -> Optional[date]:
        if v is not None and v.weekday() != 0:
            raise ValueError(
                f"week_start must be a Monday; got {v.strftime('%A')} ({v})"
            )
        return v


class WeeklyScheduleResponse(WeeklySchedule):
    class Config:
        from_attributes = True


class PaginatedWeeklyScheduleResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[WeeklyScheduleResponse]
