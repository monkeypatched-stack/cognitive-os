from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, model_validator

from services.common.models.enums import ShiftType


class BreakPeriod(BaseModel):
    """A single break within a shift."""

    break_start: time
    break_end: time
    paid: bool = False
    reason: Optional[str] = Field(
        None, max_length=100, examples=["Lunch", "Short break"]
    )

    @model_validator(mode="after")
    def end_after_start(self) -> "BreakPeriod":
        if self.break_end <= self.break_start:
            raise ValueError("break_end must be after break_start")
        return self

    @computed_field  # type: ignore[misc]
    @property
    def duration_minutes(self) -> int:
        start = datetime.combine(datetime.today(), self.break_start)
        end = datetime.combine(datetime.today(), self.break_end)
        return int((end - start).total_seconds() / 60)


class TimeSheetEntry(BaseModel):
    """A single day/shift entry for one worker."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    worker_id: str = Field(
        ..., min_length=1, max_length=50, description="Employee ID or badge number"
    )
    worker_name: str = Field(..., min_length=1, max_length=150)
    work_date: date
    shift_type: ShiftType = ShiftType.MORNING
    clock_in: datetime
    clock_out: Optional[datetime] = None
    breaks: list[BreakPeriod] = Field(default_factory=list)
    delivery_note_ids: list[str] = Field(
        default_factory=list,
        description="Delivery notes this worker was involved in",
    )
    vehicle_id: Optional[str] = Field(
        None, description="Vehicle driven / operated during this shift"
    )
    warehouse_zone: Optional[str] = Field(
        None, max_length=50, description="Zone or bay assignment"
    )
    tasks_performed: list[str] = Field(
        default_factory=list,
        examples=[["Loading", "Unloading", "Pallet wrapping", "Forklift operation"]],
    )
    overtime_hours: Annotated[float, Field(ge=0)] = 0.0
    overtime_approved_by: Optional[str] = Field(None, max_length=150)
    supervisor_id: Optional[str] = Field(None, max_length=50)
    approved: bool = False
    approved_by: Optional[str] = Field(None, max_length=150)
    approved_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def clock_out_after_clock_in(self) -> "TimeSheetEntry":
        if self.clock_out is not None and self.clock_out <= self.clock_in:
            raise ValueError("clock_out must be after clock_in")
        return self

    @model_validator(mode="after")
    def overtime_needs_approver(self) -> "TimeSheetEntry":
        if self.overtime_hours > 0 and self.overtime_approved_by is None:
            raise ValueError("overtime_approved_by is required when overtime_hours > 0")
        return self

    @computed_field  # type: ignore[misc]
    @property
    def total_break_minutes(self) -> int:
        return sum(b.duration_minutes for b in self.breaks)

    @computed_field  # type: ignore[misc]
    @property
    def gross_hours_worked(self) -> Optional[float]:
        if self.clock_out is None:
            return None
        delta = self.clock_out - self.clock_in
        return round(delta.total_seconds() / 3600, 4)

    @computed_field  # type: ignore[misc]
    @property
    def net_hours_worked(self) -> Optional[float]:
        """Gross hours minus unpaid breaks."""
        gross = self.gross_hours_worked
        if gross is None:
            return None
        unpaid_minutes = sum(b.duration_minutes for b in self.breaks if not b.paid)
        return round(gross - unpaid_minutes / 60, 4)


class TimeSheet(BaseModel):
    """Aggregated time sheet for a worker covering a pay period."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    worker_id: str = Field(..., min_length=1, max_length=50)
    worker_name: str = Field(..., min_length=1, max_length=150)
    period_start: date
    period_end: date
    entries: list[TimeSheetEntry] = Field(default_factory=list)
    total_regular_hours: float = Field(default=0.0, ge=0)
    total_overtime_hours: float = Field(default=0.0, ge=0)
    total_paid_hours: float = Field(default=0.0, ge=0)
    currency: str = Field(
        default="USD", min_length=3, max_length=3, description="ISO 4217"
    )
    hourly_rate: Optional[Annotated[Decimal, Field(gt=0)]] = None
    overtime_rate_multiplier: float = Field(default=1.5, gt=0)
    submitted: bool = False
    submitted_at: Optional[datetime] = None
    approved: bool = False
    approved_by: Optional[str] = Field(None, max_length=150)
    approved_at: Optional[datetime] = None
    payroll_reference: Optional[str] = Field(None, max_length=100)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def period_end_after_start(self) -> "TimeSheet":
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self

    @computed_field  # type: ignore[misc]
    @property
    def gross_pay(self) -> Optional[Decimal]:
        if self.hourly_rate is None:
            return None
        regular_pay = Decimal(str(self.total_regular_hours)) * self.hourly_rate
        overtime_pay = (
            Decimal(str(self.total_overtime_hours))
            * self.hourly_rate
            * Decimal(str(self.overtime_rate_multiplier))
        )
        return round(regular_pay + overtime_pay, 2)

    def recalculate_totals(self) -> None:
        """Recompute hour totals from entries in place."""
        regular = 0.0
        overtime = 0.0
        for entry in self.entries:
            net = entry.net_hours_worked or 0.0
            ot = entry.overtime_hours
            regular += max(0.0, net - ot)
            overtime += ot
        self.total_regular_hours = round(regular, 4)
        self.total_overtime_hours = round(overtime, 4)
        self.total_paid_hours = round(regular + overtime, 4)


class TimeSheetCreate(TimeSheet):
    pass


class TimeSheetUpdate(BaseModel):
    worker_id: Optional[str] = Field(None, min_length=1, max_length=50)
    worker_name: Optional[str] = Field(None, min_length=1, max_length=150)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    entries: Optional[list[TimeSheetEntry]] = None
    total_regular_hours: Optional[float] = Field(None, ge=0)
    total_overtime_hours: Optional[float] = Field(None, ge=0)
    total_paid_hours: Optional[float] = Field(None, ge=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    hourly_rate: Optional[Annotated[Decimal, Field(gt=0)]] = None
    overtime_rate_multiplier: Optional[float] = Field(None, gt=0)
    submitted: Optional[bool] = None
    submitted_at: Optional[datetime] = None
    approved: Optional[bool] = None
    approved_by: Optional[str] = Field(None, max_length=150)
    approved_at: Optional[datetime] = None
    payroll_reference: Optional[str] = Field(None, max_length=100)
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}


class TimeSheetResponse(TimeSheet):
    class Config:
        from_attributes = True


class PaginatedTimeSheetResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[TimeSheetResponse]
