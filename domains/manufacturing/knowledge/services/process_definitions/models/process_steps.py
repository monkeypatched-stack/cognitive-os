"""
models/process_steps.py
------------------------
Execution step models that define the body of a process_definition.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from services.process_definitions.models.process_constraints import (
    ProcessStepConstraints,
)
from services.process_definitions.models.process_corrections import CorrectiveActions
from services.process_definitions.models.process_post_checks import (
    ProcessStepPostchecks,
)
from services.process_definitions.models.process_pre_checks import ProcessStepPrechecks
from services.process_definitions.models.process_definition_common import (
    Status,
    ActionType,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Step sub-models
# ---------------------------------------------------------------------------


class StepInput(BaseModel):
    name: str = Field(..., min_length=1)
    value: Optional[str] = None
    description: Optional[str] = None
    is_required: bool = True


class StepInputUpdate(BaseModel):
    name: Optional[str] = None
    value: Optional[str] = None
    description: Optional[str] = None
    is_required: Optional[bool] = None


class StepOutput(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    value: Optional[str] = None


class StepOutputUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    value: Optional[str] = None


# ---------------------------------------------------------------------------
# ProcessStep
# ---------------------------------------------------------------------------


class ProcessStep(BaseModel):
    id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=1)
    name: str = Field(..., min_length=1)
    description: str = Field(...)
    action_type: ActionType = Field(default=ActionType.MANUAL)
    command: Optional[str] = None
    inputs: List[StepInput] = Field(default_factory=list)
    outputs: List[StepOutput] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list)
    is_optional: bool = False
    timeout_seconds: Optional[int] = None
    retry_count: int = Field(default=0, ge=0)
    status: Status = Field(default=Status.PENDING)
    prechecks: ProcessStepPrechecks
    postchecks: ProcessStepPostchecks
    constraints: ProcessStepConstraints
    corrective_actions: CorrectiveActions
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessStep":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProcessStepCreate(ProcessStep):
    pass


class ProcessStepUpdate(BaseModel):
    sequence: Optional[int] = Field(None, ge=1)
    name: Optional[str] = None
    description: Optional[str] = None
    action_type: Optional[ActionType] = None
    command: Optional[str] = None
    inputs: Optional[List[StepInput]] = None
    outputs: Optional[List[StepOutput]] = None
    depends_on: Optional[List[str]] = None
    is_optional: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    retry_count: Optional[int] = Field(None, ge=0)
    status: Optional[Status] = None
    prechecks: Optional[ProcessStepPrechecks] = None
    postchecks: Optional[ProcessStepPostchecks] = None
    constraints: Optional[ProcessStepConstraints] = None
    corrective_actions: Optional[CorrectiveActions] = None
    notes: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ProcessStepResponse(ProcessStep):
    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# ProcessSteps (collection)
# ---------------------------------------------------------------------------


class ProcessSteps(BaseModel):
    id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    description: Optional[str] = None
    steps: List[ProcessStep] = Field(default_factory=list)
    allow_parallel_execution: bool = False
    rollback_on_failure: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessSteps":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProcessStepsCreate(ProcessSteps):
    pass


class ProcessStepsUpdate(BaseModel):
    description: Optional[str] = None
    steps: Optional[List[ProcessStep]] = None
    allow_parallel_execution: Optional[bool] = None
    rollback_on_failure: Optional[bool] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ProcessStepsResponse(ProcessSteps):
    class Config:
        from_attributes = True


class PaginatedProcessStepsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProcessStepsResponse]
