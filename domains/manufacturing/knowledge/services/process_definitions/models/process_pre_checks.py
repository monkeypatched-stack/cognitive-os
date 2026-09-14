"""
models/process_pre_checks.py
-----------------------------
Pre-condition models that gate process_definition execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from services.process_definitions.models.process_definition_common import (
    ConditionOperator,
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
# PreCheckCondition
# ---------------------------------------------------------------------------


class PreCheckCondition(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = Field(...)
    operator: ConditionOperator = Field(...)
    expected_value: Optional[str] = None
    is_mandatory: bool = True
    severity: Severity = Field(default=Severity.HIGH)
    check_command: Optional[str] = None
    remediation_hint: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "PreCheckCondition":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class PreCheckConditionCreate(PreCheckCondition):
    pass


class PreCheckConditionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    operator: Optional[ConditionOperator] = None
    expected_value: Optional[str] = None
    is_mandatory: Optional[bool] = None
    severity: Optional[Severity] = None
    check_command: Optional[str] = None
    remediation_hint: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)


class PreCheckConditionResponse(PreCheckCondition):
    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# ProcessPrechecks
# ---------------------------------------------------------------------------


class ProcessPrechecks(BaseModel):
    id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    description: Optional[str] = None
    conditions: List[PreCheckCondition] = Field(default_factory=list)
    all_must_pass: bool = True
    timeout_seconds: Optional[int] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessPrechecks":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProcessPrechecksCreate(ProcessPrechecks):
    pass


class ProcessPrechecksUpdate(BaseModel):
    description: Optional[str] = None
    conditions: Optional[List[PreCheckCondition]] = None
    all_must_pass: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ProcessPrechecksResponse(ProcessPrechecks):
    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# ProcessStepPrechecks
# ---------------------------------------------------------------------------


class ProcessStepPrechecks(BaseModel):
    id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    process_step_id: str = Field(..., min_length=1)
    description: Optional[str] = None
    conditions: List[PreCheckCondition] = Field(default_factory=list)
    all_must_pass: bool = True
    timeout_seconds: Optional[int] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessStepPrechecks":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProcessStepPrechecksCreate(ProcessStepPrechecks):
    pass


class ProcessStepPrechecksUpdate(BaseModel):
    description: Optional[str] = None
    conditions: Optional[List[PreCheckCondition]] = None
    all_must_pass: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ProcessStepPrechecksResponse(ProcessStepPrechecks):
    class Config:
        from_attributes = True


class PaginatedProcessPrechecksResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProcessPrechecksResponse]


class PaginatedProcessStepPrechecksResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProcessStepPrechecksResponse]
