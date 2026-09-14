from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from services.changeover.models.changeover_tasks import ChangeoverTask
from services.common.models.enums import ChangeoverStatus, ChangeoverType, TaskCategory


class ChangeoverProcedure(BaseModel):
    """Reusable SOP template for a product/workstation changeover."""

    id: str = None
    plant_id: str
    name: str = Field(..., min_length=2, max_length=120)
    changeover_type: ChangeoverType
    from_product_id: Optional[str] = Field(
        None, description="None = applies to any source product"
    )
    to_product_id: Optional[str] = Field(
        None, description="None = applies to any target product"
    )
    applicable_workstation_ids: list[str] = Field(
        default_factory=list,
        description="Empty list = applies to all workstations",
    )
    tasks: list[ChangeoverTask] = Field(..., min_length=1)
    version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    active: bool = True
    status: ChangeoverStatus = ChangeoverStatus.PLANNED
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("changeover_type", "status", mode="before")
    @classmethod
    def normalize_enum_value(cls, value):
        return value.lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def unique_sequences(self):
        seqs = [t.sequence for t in self.tasks]
        if len(seqs) != len(set(seqs)):
            raise ValueError("Task sequence numbers must be unique within a procedure")
        if all(t.is_complete for t in self.tasks):
            self.status = ChangeoverStatus.COMPLETED
        return self

    @computed_field  # type: ignore[misc]
    @property
    def total_estimated_minutes(self) -> int:
        return sum(t.estimated_minutes for t in self.tasks)

    @computed_field  # type: ignore[misc]
    @property
    def internal_minutes(self) -> int:
        return sum(
            t.estimated_minutes
            for t in self.tasks
            if t.category == TaskCategory.INTERNAL
        )

    @computed_field  # type: ignore[misc]
    @property
    def external_minutes(self) -> int:
        return sum(
            t.estimated_minutes
            for t in self.tasks
            if t.category == TaskCategory.EXTERNAL
        )

    @computed_field  # type: ignore[misc]
    @property
    def completed_task_count(self) -> int:
        return sum(1 for task in self.tasks if task.is_complete)

    @computed_field  # type: ignore[misc]
    @property
    def completion_percent(self) -> float:
        if not self.tasks:
            return 0.0
        return round((self.completed_task_count / len(self.tasks)) * 100, 2)


class ChangeoverProcedureCreate(ChangeoverProcedure):
    pass


class ChangeoverProcedureUpdate(BaseModel):
    plant_id: Optional[str] = None
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    changeover_type: Optional[ChangeoverType] = None
    from_product_id: Optional[str] = None
    to_product_id: Optional[str] = None
    applicable_workstation_ids: Optional[list[str]] = None
    tasks: Optional[list[ChangeoverTask]] = Field(None, min_length=1)
    version: Optional[str] = Field(None, pattern=r"^\d+\.\d+$")
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    active: Optional[bool] = None
    status: Optional[ChangeoverStatus] = None
    completed_at: Optional[datetime] = None

    @field_validator("changeover_type", "status", mode="before")
    @classmethod
    def normalize_enum_value(cls, value):
        return value.lower() if isinstance(value, str) else value


class ChangeoverProcedureResponse(ChangeoverProcedure):
    class Config:
        from_attributes = True


class PaginatedChangeoverProcedureResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ChangeoverProcedureResponse]
