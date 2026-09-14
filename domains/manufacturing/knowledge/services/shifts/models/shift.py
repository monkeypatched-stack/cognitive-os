# ---------------------------------------------------------------------------
# Shift Schedule  (a concrete dated shift occurrence)
# ---------------------------------------------------------------------------

from datetime import date, datetime
from typing import Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, computed_field

from services.common.models.enums import ShiftType
from services.shifts.models.time_range import TimeRange


class ShiftSchedule(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    id: UUID = Field(default_factory=uuid4)
    plant_id: str
    template_id: Optional[UUID] = Field(
        None, description="Source ShiftTemplate, if generated from one"
    )
    shift_date: date
    shift_type: ShiftType
    time_range: TimeRange
    supervisor_id: UUID = Field(..., description="Responsible supervisor employee id")
    employee_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field  # type: ignore[misc]
    @property
    def headcount(self) -> int:
        return len(self.employee_ids)


class ShiftScheduleCreate(ShiftSchedule):
    pass


class ShiftScheduleUpdate(BaseModel):
    plant_id: Optional[str] = None
    template_id: Optional[UUID] = None
    shift_date: Optional[date] = None
    shift_type: Optional[ShiftType] = None
    time_range: Optional[TimeRange] = None
    supervisor_id: Optional[UUID] = None
    employee_ids: Optional[list[UUID]] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ShiftScheduleResponse(ShiftSchedule):
    class Config:
        from_attributes = True


class PaginatedShiftScheduleResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ShiftScheduleResponse]
