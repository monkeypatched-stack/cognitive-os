from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from services.changeover.models.changeover_tasks import ChangeoverTask
from services.common.models.enums import (
    ChangeoverStatus,
    ChangeoverTrigger,
    ChangeoverType,
    TaskStatus,
)


class ChangeoverEvent(BaseModel):
    """Concrete live or historical workstation changeover."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    workstation_id: str
    factory_id: str
    procedure_id: Optional[str] = Field(
        None, description="Source ChangeoverProcedure template"
    )
    shift_schedule_id: Optional[str] = Field(
        None, description="Shift during which the changeover occurs"
    )
    changeover_type: ChangeoverType
    trigger: ChangeoverTrigger = ChangeoverTrigger.SCHEDULED
    status: ChangeoverStatus = ChangeoverStatus.PLANNED
    from_product_id: Optional[str] = None
    to_product_id: Optional[str] = None
    planned_start: datetime
    planned_end: datetime
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    lead_operator_id: Optional[str] = None
    team_member_ids: list[str] = Field(default_factory=list)
    tasks: list[ChangeoverTask] = Field(default_factory=list)
    downtime_minutes: Optional[float] = Field(
        None, ge=0, description="Unplanned stoppage time added during changeover"
    )
    quality_check_passed: Optional[bool] = Field(
        None, description="First-article / first-off quality result after changeover"
    )
    notes: Optional[str] = Field(None, max_length=800)

    @field_validator("changeover_type", "trigger", "status", mode="before")
    @classmethod
    def normalize_enum_value(cls, value):
        return value.lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def planned_end_after_start(self):
        if self.planned_end <= self.planned_start:
            raise ValueError("planned_end must be after planned_start")
        return self

    @model_validator(mode="after")
    def actual_end_after_actual_start(self):
        if (
            self.actual_start
            and self.actual_end
            and self.actual_end <= self.actual_start
        ):
            raise ValueError("actual_end must be after actual_start")
        return self

    @model_validator(mode="after")
    def completed_needs_actual_times(self):
        if self.status == ChangeoverStatus.COMPLETED and (
            not self.actual_start or not self.actual_end
        ):
            raise ValueError(
                "status=completed requires both actual_start and actual_end"
            )
        return self

    @computed_field  # type: ignore[misc]
    @property
    def planned_duration_minutes(self) -> float:
        return (self.planned_end - self.planned_start).total_seconds() / 60

    @computed_field  # type: ignore[misc]
    @property
    def actual_duration_minutes(self) -> Optional[float]:
        if self.actual_start and self.actual_end:
            return (self.actual_end - self.actual_start).total_seconds() / 60
        return None

    @computed_field  # type: ignore[misc]
    @property
    def variance_minutes(self) -> Optional[float]:
        if self.actual_duration_minutes is not None:
            return self.actual_duration_minutes - self.planned_duration_minutes
        return None

    @computed_field  # type: ignore[misc]
    @property
    def completion_pct(self) -> float:
        if not self.tasks:
            return 0.0
        done = sum(
            1 for t in self.tasks if t.status in {TaskStatus.DONE, TaskStatus.SKIPPED}
        )
        return round(done / len(self.tasks) * 100, 1)

    @computed_field  # type: ignore[misc]
    @property
    def is_running_late(self) -> bool:
        if self.status != ChangeoverStatus.IN_PROGRESS or not self.actual_start:
            return False
        elapsed = (datetime.utcnow() - self.actual_start).total_seconds() / 60
        return elapsed > self.planned_duration_minutes

    def pending_tasks(self) -> list[ChangeoverTask]:
        return [t for t in self.tasks if t.status == TaskStatus.PENDING]

    def blocked_tasks(self) -> list[ChangeoverTask]:
        return [t for t in self.tasks if t.status == TaskStatus.BLOCKED]


class ChangeoverEventCreate(ChangeoverEvent):
    pass


class ChangeoverEventUpdate(BaseModel):
    workstation_id: Optional[str] = None
    factory_id: Optional[str] = None
    procedure_id: Optional[str] = None
    shift_schedule_id: Optional[str] = None
    changeover_type: Optional[ChangeoverType] = None
    trigger: Optional[ChangeoverTrigger] = None
    status: Optional[ChangeoverStatus] = None
    from_product_id: Optional[str] = None
    to_product_id: Optional[str] = None
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    lead_operator_id: Optional[str] = None
    team_member_ids: Optional[list[str]] = None
    tasks: Optional[list[ChangeoverTask]] = None
    downtime_minutes: Optional[float] = Field(None, ge=0)
    quality_check_passed: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=800)

    @field_validator("changeover_type", "trigger", "status", mode="before")
    @classmethod
    def normalize_enum_value(cls, value):
        return value.lower() if isinstance(value, str) else value


class ChangeoverEventResponse(ChangeoverEvent):
    class Config:
        from_attributes = True


class PaginatedChangeoverEventResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ChangeoverEventResponse]
