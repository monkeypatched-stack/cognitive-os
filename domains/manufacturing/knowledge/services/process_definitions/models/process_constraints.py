"""
models/process_constraints.py
------------------------------
Constraint models that define what is forbidden during process_definition execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from services.process_definitions.models.process_definition_common import (
    ConstraintType,
    Severity,
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
# Constraint
# ---------------------------------------------------------------------------


class Constraint(BaseModel):
    id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = Field(...)
    constraint_type: ConstraintType = Field(...)
    is_hard_constraint: bool = True
    severity: Severity = Field(default=Severity.CRITICAL)
    enforcement_mechanism: Optional[str] = None
    violation_message: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "Constraint":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ConstraintCreate(Constraint):
    pass


class ConstraintUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    constraint_type: Optional[ConstraintType] = None
    is_hard_constraint: Optional[bool] = None
    severity: Optional[Severity] = None
    enforcement_mechanism: Optional[str] = None
    violation_message: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ConstraintResponse(Constraint):
    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# ProcessConstraints
# ---------------------------------------------------------------------------


class ProcessConstraints(BaseModel):
    id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    constraints: List[Constraint] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessConstraints":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProcessConstraintsCreate(ProcessConstraints):
    pass


class ProcessConstraintsUpdate(BaseModel):
    constraints: Optional[List[Constraint]] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ProcessConstraintsResponse(ProcessConstraints):
    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# ProcessStepConstraints
# ---------------------------------------------------------------------------


class ProcessStepConstraints(BaseModel):
    id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    process_step_id: str = Field(..., min_length=1)
    constraints: List[Constraint] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessStepConstraints":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProcessStepConstraintsCreate(ProcessStepConstraints):
    pass


class ProcessStepConstraintsUpdate(BaseModel):
    constraints: Optional[List[Constraint]] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ProcessStepConstraintsResponse(ProcessStepConstraints):
    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Paginated responses
# ---------------------------------------------------------------------------


class PaginatedProcessConstraintsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProcessConstraintsResponse]


class PaginatedProcessStepConstraintsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProcessStepConstraintsResponse]
