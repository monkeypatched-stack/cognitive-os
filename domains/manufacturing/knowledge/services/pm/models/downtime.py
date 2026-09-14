from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional
from datetime import datetime
from enum import Enum
import uuid

# ─────────────────────────────────────────────
# ENUMS (match your TS types)
# ─────────────────────────────────────────────


class DowntimeCategory(str, Enum):
    MECHANICAL_FAILURE = "Mechanical Failure"
    ELECTRICAL_FAULT = "Electrical Fault"
    PLANNED_MAINTENANCE = "Planned Maintenance"
    SOFTWARE_ERROR = "Software Error"
    OPERATOR_ERROR = "Operator Error"
    MATERIAL_SHORTAGE = "Material Shortage"
    QUALITY_ISSUE = "Quality Issue"
    EXTERNAL = "External"


class DowntimeStatus(str, Enum):
    ACTIVE = "Active"
    RESOLVED = "Resolved"
    SCHEDULED = "Scheduled"


# ─────────────────────────────────────────────
# MAIN MODEL (DB Model)
# ─────────────────────────────────────────────


class DowntimeLog(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None
    machine_name: Optional[str] = None
    line_id: Optional[str] = None

    start_time: datetime
    end_time: Optional[datetime] = None

    duration_minutes: Optional[int] = None  # computed

    category: DowntimeCategory
    cause: Optional[str] = None
    status: DowntimeStatus = DowntimeStatus.ACTIVE

    responsible_team: Optional[str] = None
    linked_work_order: Optional[str] = None
    linked_maintenance_id: Optional[str] = None

    notes: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    # ─────────────────────────────────────────
    # VALIDATION
    # ─────────────────────────────────────────

    @model_validator(mode="after")
    def validate_and_compute(self):
        # Ensure end_time is after start_time
        if self.end_time and self.end_time < self.start_time:
            raise ValueError("end_time must be after start_time")

        # Compute duration
        if self.start_time and self.end_time:
            delta = self.end_time - self.start_time
            self.duration_minutes = int(delta.total_seconds() // 60)

        return self


class DowntimeLogCreate(DowntimeLog):
    pass


class DowntimeLogUpdate(BaseModel):
    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None
    machine_name: Optional[str] = None
    line_id: Optional[str] = None

    start_time: datetime
    end_time: Optional[datetime] = None

    duration_minutes: Optional[int] = None  # computed

    category: DowntimeCategory
    cause: Optional[str] = None
    status: DowntimeStatus = DowntimeStatus.ACTIVE

    responsible_team: Optional[str] = None
    linked_work_order: Optional[str] = None
    linked_maintenance_id: Optional[str] = None

    notes: Optional[str] = None
    updated_at: Optional[datetime] = None


class DowntimeLogResponse(DowntimeLog):
    class Config:
        from_attributes = True


class PaginatedDowntimeResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[DowntimeLog]
