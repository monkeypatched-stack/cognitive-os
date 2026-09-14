from datetime import date, datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

ToolCategory = Literal[
    "Hand-Tool",
    "Power-Tool",
    "Measurement-Device",
    "Calibration-Standard",
    "Safety-Equipment",
    "Specialized-Apparatus",
    "Consumable-Accessory",
]
ToolStatus = Literal[
    "Available",
    "Checked-Out",
    "Under-Maintenance",
    "Calibration-Due",
    "Quarantined",
    "Lost",
    "Retired",
]
AssignmentScope = Literal[
    "Tool-Crib",
    "User",
    "Team/Department",
    "Zone/Workstation",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class ToolBase(BaseModel):
    tool_code: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    category: ToolCategory = "Hand-Tool"
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    status: ToolStatus = "Available"

    measurement_range: Optional[str] = None
    unit: Optional[str] = None
    resolution: Optional[str] = None
    accuracy_class: Optional[str] = None
    calibration_required: bool = False

    total_quantity: int = Field(default=1, ge=0)
    min_stock_level: Optional[int] = Field(default=None, ge=0)

    assignment_scope: AssignmentScope = "Tool-Crib"
    assigned_to_id: Optional[str] = None
    assigned_to_name: Optional[str] = None
    checked_out_date: Optional[date] = None
    due_return_date: Optional[date] = None
    last_location_id: Optional[str] = None

    last_calibration_date: Optional[date] = None
    next_calibration_date: Optional[date] = None
    calibration_interval_days: Optional[int] = Field(default=None, gt=0)
    last_service_date: Optional[date] = None
    next_service_date: Optional[date] = None
    service_notes: Optional[str] = None

    requires_training: bool = False
    hazard_warnings: Optional[list[str]] = None
    remarks: Optional[str] = None
    attachments: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates(self) -> "ToolBase":
        if self.last_calibration_date and self.next_calibration_date:
            if self.next_calibration_date < self.last_calibration_date:
                raise ValueError(
                    "next_calibration_date cannot be before last_calibration_date"
                )
        if self.last_service_date and self.next_service_date:
            if self.next_service_date < self.last_service_date:
                raise ValueError("next_service_date cannot be before last_service_date")
        if self.checked_out_date and self.due_return_date:
            if self.due_return_date < self.checked_out_date:
                raise ValueError("due_return_date cannot be before checked_out_date")
        return self


class ToolCreate(ToolBase):
    tool_id: Optional[str] = None


class ToolRecord(ToolBase):
    tool_id: str = Field(..., min_length=1)
    available_quantity: int = Field(default=0, ge=0)
    checked_out_quantity: int = Field(default=0, ge=0)
    is_overdue_return: bool = False
    is_calibration_due: bool = False
    utilization_pct: Optional[float] = None
    days_since_last_use: Optional[int] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_compute(self) -> "ToolRecord":
        today = date.today()
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        self.checked_out_quantity = min(self.checked_out_quantity, self.total_quantity)
        self.available_quantity = max(
            self.total_quantity - self.checked_out_quantity, 0
        )
        self.is_overdue_return = (
            self.due_return_date is not None
            and self.due_return_date < today
            and self.status == "Checked-Out"
        )
        self.is_calibration_due = (
            self.calibration_required
            and self.next_calibration_date is not None
            and self.next_calibration_date <= today
        ) or self.status == "Calibration-Due"
        self.utilization_pct = (
            round((self.checked_out_quantity / self.total_quantity) * 100, 2)
            if self.total_quantity > 0
            else None
        )
        self.days_since_last_use = (
            (today - self.checked_out_date).days
            if self.checked_out_date is not None
            else None
        )
        return self


class ToolUpdate(BaseModel):
    tool_code: Optional[str] = Field(default=None, min_length=1)
    name: Optional[str] = Field(default=None, min_length=1)
    category: Optional[ToolCategory] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    status: Optional[ToolStatus] = None
    measurement_range: Optional[str] = None
    unit: Optional[str] = None
    resolution: Optional[str] = None
    accuracy_class: Optional[str] = None
    calibration_required: Optional[bool] = None
    total_quantity: Optional[int] = Field(default=None, ge=0)
    checked_out_quantity: Optional[int] = Field(default=None, ge=0)
    min_stock_level: Optional[int] = Field(default=None, ge=0)
    assignment_scope: Optional[AssignmentScope] = None
    assigned_to_id: Optional[str] = None
    assigned_to_name: Optional[str] = None
    checked_out_date: Optional[date] = None
    due_return_date: Optional[date] = None
    last_location_id: Optional[str] = None
    last_calibration_date: Optional[date] = None
    next_calibration_date: Optional[date] = None
    calibration_interval_days: Optional[int] = Field(default=None, gt=0)
    last_service_date: Optional[date] = None
    next_service_date: Optional[date] = None
    service_notes: Optional[str] = None
    requires_training: Optional[bool] = None
    hazard_warnings: Optional[list[str]] = None
    remarks: Optional[str] = None
    attachments: Optional[list[str]] = None


class PaginatedToolResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ToolRecord]
