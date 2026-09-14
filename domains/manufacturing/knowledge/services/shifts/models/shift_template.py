from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, model_validator

from services.common.models.enums import DayOfWeek, ShiftType
from services.shifts.models.time_range import TimeRange


class BreakSlot(BaseModel):
    """A scheduled break within a shift."""

    label: str = Field(..., examples=["Lunch", "Tea Break"])
    time_range: TimeRange
    paid: bool = False


class ShiftTemplate(BaseModel):
    """
    A reusable shift definition (e.g. 'Morning Shift Mon-Fri').
    Concrete ShiftSchedule records reference this.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    plant_id: str
    name: str = Field(..., min_length=2, max_length=80)
    shift_type: ShiftType
    time_range: TimeRange
    days: list[DayOfWeek] = Field(..., min_length=1)
    breaks: list[BreakSlot] = Field(default_factory=list)
    color_hex: str = Field(
        default="#3B82F6",
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="UI colour for calendar views",
    )

    @computed_field  # type: ignore[misc]
    @property
    def net_work_minutes(self) -> int:
        """Shift duration minus all unpaid breaks."""
        unpaid_break_mins = sum(
            b.time_range.duration_minutes for b in self.breaks if not b.paid
        )
        return self.time_range.duration_minutes - unpaid_break_mins

    @model_validator(mode="after")
    def no_overlapping_breaks(self):
        sorted_breaks = sorted(self.breaks, key=lambda b: b.time_range.start)
        for i in range(len(sorted_breaks) - 1):
            if sorted_breaks[i].time_range.end > sorted_breaks[i + 1].time_range.start:
                raise ValueError(
                    f"Break '{sorted_breaks[i].label}' overlaps with "
                    f"'{sorted_breaks[i + 1].label}'"
                )
        return self


class ShiftTemplateCreate(ShiftTemplate):
    pass


class ShiftTemplateUpdate(BaseModel):
    plant_id: Optional[str] = None
    name: Optional[str] = Field(None, min_length=2, max_length=80)
    shift_type: Optional[ShiftType] = None
    time_range: Optional[TimeRange] = None
    days: Optional[list[DayOfWeek]] = Field(None, min_length=1)
    breaks: Optional[list[BreakSlot]] = None
    color_hex: Optional[str] = Field(
        None,
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="UI colour for calendar views",
    )


class ShiftTemplateResponse(ShiftTemplate):
    class Config:
        from_attributes = True


class PaginatedShiftTemplateResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ShiftTemplateResponse]
