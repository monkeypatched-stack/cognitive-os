from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from services.common.models.enums import TaskCategory, TaskStatus


class ChangeoverTask(BaseModel):
    """One atomic step inside a changeover procedure or a live changeover."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    sequence: int = Field(..., ge=1, description="Execution order (1-based)")
    title: str = Field(
        ...,
        validation_alias=AliasChoices("title", "name"),
        min_length=2,
        max_length=120,
    )
    description: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("description", "instructions"),
        max_length=600,
    )
    category: TaskCategory = TaskCategory.INTERNAL
    estimated_minutes: int = Field(..., ge=1)
    assigned_to: Optional[str] = Field(
        None, description="Employee ID responsible for this task"
    )
    tools_required: list[str] = Field(
        default_factory=list, examples=[["Torque wrench 40 Nm", "Hex key set"]]
    )
    is_complete: bool = False
    status: TaskStatus = TaskStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    blocked_reason: Optional[str] = Field(None, max_length=300)

    @field_validator("category", "status", mode="before")
    @classmethod
    def normalize_enum_value(cls, value):
        return value.lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def completed_requires_start(self):
        if self.status == TaskStatus.DONE or self.completed_at is not None:
            self.is_complete = True
        if self.is_complete:
            self.status = TaskStatus.DONE
        if self.completed_at is not None and self.started_at is None:
            raise ValueError("completed_at requires started_at to be set first")
        if (
            self.completed_at is not None
            and self.started_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("completed_at cannot be before started_at")
        return self

    @computed_field  # type: ignore[misc]
    @property
    def actual_minutes(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds() / 60
        return None

    @computed_field  # type: ignore[misc]
    @property
    def is_overdue(self) -> bool:
        if self.status in {TaskStatus.DONE, TaskStatus.SKIPPED}:
            return False
        if self.started_at and self.actual_minutes is None:
            elapsed = (datetime.utcnow() - self.started_at).total_seconds() / 60
            return elapsed > self.estimated_minutes
        return False


class ChangeoverTaskCreate(ChangeoverTask):
    pass


class ChangeoverTaskUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sequence: Optional[int] = Field(None, ge=1)
    title: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("title", "name"),
        min_length=2,
        max_length=120,
    )
    description: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("description", "instructions"),
        max_length=600,
    )
    category: Optional[TaskCategory] = None
    estimated_minutes: Optional[int] = Field(None, ge=1)
    assigned_to: Optional[str] = None
    tools_required: Optional[list[str]] = None
    is_complete: Optional[bool] = None
    status: Optional[TaskStatus] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    blocked_reason: Optional[str] = Field(None, max_length=300)

    @field_validator("category", "status", mode="before")
    @classmethod
    def normalize_enum_value(cls, value):
        return value.lower() if isinstance(value, str) else value
