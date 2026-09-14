from datetime import date, datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field, field_serializer, model_validator

MaintenanceType = Literal["Preventive", "Corrective", "Predictive", "Inspection"]
MaintenanceStatus = Literal[
    "Scheduled", "In Progress", "Completed", "Cancelled", "Delayed"
]
SeverityLevel = Literal["Low", "Medium", "High", "Critical"]


# ─── Nested models ────────────────────────────────────────────────────────────


class Downtime(BaseModel):
    start_time: str
    end_time: Optional[str] = None


class Technician(BaseModel):
    id: str
    name: str
    skill: Optional[str] = None
    shift: Optional[str] = None


class PartUsed(BaseModel):
    part_id: str
    name: str
    quantity: int = Field(..., gt=0)
    cost_per_unit: float = Field(..., gt=0)


# ─── Base ─────────────────────────────────────────────────────────────────────


class MaintenanceLog(BaseModel):
    id: str
    machine_id: str
    equipment_id: Optional[str] = None
    machine_name: Optional[str] = None
    maintenance_type: MaintenanceType
    status: MaintenanceStatus
    severity: Optional[SeverityLevel] = None
    description: Optional[str] = None
    issue_reported: Optional[str] = None
    resolution: Optional[str] = None
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    actual_start: Optional[str] = None
    actual_end: Optional[str] = None
    next_due_date: Optional[date] = None
    maintenance_interval_days: Optional[int] = Field(None, gt=0)
    performed_by: Optional[str] = None
    reviewed_by: Optional[str] = None
    approved_by: Optional[str] = None
    downtime: Optional[Downtime] = None
    parts_used: list[PartUsed] = []
    work_order_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @field_serializer("next_due_date")
    def serialize_date(self, value: date | None):
        if value is None:
            return None
        return datetime.combine(value, datetime.min.time())

    @model_validator(mode="after")
    def check_constraints(self) -> "MaintenanceLog":
        return self


class MaintenanceLogCreate(MaintenanceLog):
    @model_validator(mode="after")
    def check_create_constraints(self) -> "MaintenanceLogCreate":
        if self.status == "Completed":
            if not self.actual_start:
                raise ValueError("actual_start is required when status is 'Completed'")
            if not self.actual_end:
                raise ValueError("actual_end is required when status is 'Completed'")
            if not self.resolution:
                raise ValueError("resolution is required when status is 'Completed'")
        if self.status == "In Progress":
            if not self.actual_start:
                raise ValueError(
                    "actual_start is required when status is 'In Progress'"
                )
            if self.actual_end:
                raise ValueError(
                    "actual_end must be empty when status is 'In Progress'"
                )
        if (
            self.maintenance_type in ("Corrective", "Predictive")
            and not self.issue_reported
        ):
            raise ValueError(
                f"issue_reported is required for '{self.maintenance_type}' maintenance"
            )
        if self.maintenance_type == "Corrective" and not self.severity:
            raise ValueError("severity is required for 'Corrective' maintenance")
        if self.downtime and not self.actual_start:
            raise ValueError("downtime cannot be set without actual_start")
        return self


class MaintenanceLogUpdate(BaseModel):
    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None
    machine_name: Optional[str] = None
    maintenance_type: Optional[MaintenanceType] = None
    status: Optional[MaintenanceStatus] = None
    severity: Optional[SeverityLevel] = None
    description: Optional[str] = None
    issue_reported: Optional[str] = None
    resolution: Optional[str] = None
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    actual_start: Optional[str] = None
    actual_end: Optional[str] = None
    downtime: Optional[Downtime] = None
    technicians: Optional[list[Technician]] = None
    parts_used: Optional[list[PartUsed]] = None
    work_order_id: Optional[str] = None

    @model_validator(mode="after")
    def check_constraints(self) -> "MaintenanceLogUpdate":
        # Only validate Completed rules when all three fields are present
        if self.status == "Completed":
            if self.actual_start is not None and not self.actual_start:
                raise ValueError(
                    "actual_start cannot be empty when status is 'Completed'"
                )
            if self.actual_end is not None and not self.actual_end:
                raise ValueError(
                    "actual_end cannot be empty when status is 'Completed'"
                )
            if self.resolution is not None and not self.resolution:
                raise ValueError(
                    "resolution cannot be empty when status is 'Completed'"
                )

        # In Progress — actual_end must not be set
        if self.status == "In Progress" and self.actual_end:
            raise ValueError("actual_end must be empty when status is 'In Progress'")

        # Corrective issue_reported — only when both fields are present
        if (
            self.maintenance_type in ("Corrective", "Predictive")
            and self.issue_reported is not None
            and not self.issue_reported
        ):
            raise ValueError(
                f"issue_reported cannot be empty for '{self.maintenance_type}' maintenance"
            )

        return self


class MaintenanceLogResponse(MaintenanceLog):
    class Config:
        from_attributes = True


class PaginatedMaintenanceLogResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[MaintenanceLogResponse]
