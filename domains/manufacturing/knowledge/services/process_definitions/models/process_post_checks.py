"""
models/process_post_checks.py
------------------------------
Post-condition models that verify a process_definition completed successfully.
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
# PostCheckCondition
# ---------------------------------------------------------------------------


class PostCheckCondition(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = Field(...)
    operator: ConditionOperator = Field(...)
    expected_value: Optional[str] = None
    is_mandatory: bool = True
    severity: Severity = Field(default=Severity.HIGH)
    check_command: Optional[str] = None
    links_to_corrective_action_ids: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "PostCheckCondition":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class PostCheckConditionCreate(PostCheckCondition):
    pass


class PostCheckConditionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    operator: Optional[ConditionOperator] = None
    expected_value: Optional[str] = None
    is_mandatory: Optional[bool] = None
    severity: Optional[Severity] = None
    check_command: Optional[str] = None
    links_to_corrective_action_ids: Optional[List[str]] = None
    updated_at: datetime = Field(default_factory=utc_now)


class PostCheckConditionResponse(PostCheckCondition):
    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# ProcessPostchecks
# ---------------------------------------------------------------------------


class ProcessPostchecks(BaseModel):
    id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    description: Optional[str] = None
    conditions: List[PostCheckCondition] = Field(default_factory=list)
    all_must_pass: bool = True
    timeout_seconds: Optional[int] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessPostchecks":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProcessPostchecksCreate(ProcessPostchecks):
    pass


class ProcessPostchecksUpdate(BaseModel):
    description: Optional[str] = None
    conditions: Optional[List[PostCheckCondition]] = None
    all_must_pass: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ProcessPostchecksResponse(ProcessPostchecks):
    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# ProcessStepPostchecks
# ---------------------------------------------------------------------------


class ProcessStepPostchecks(BaseModel):
    id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    process_step_id: str = Field(..., min_length=1)
    description: Optional[str] = None
    conditions: List[PostCheckCondition] = Field(default_factory=list)
    all_must_pass: bool = True
    timeout_seconds: Optional[int] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessStepPostchecks":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProcessStepPostchecksCreate(ProcessStepPostchecks):
    pass


class ProcessStepPostchecksUpdate(BaseModel):
    description: Optional[str] = None
    conditions: Optional[List[PostCheckCondition]] = None
    all_must_pass: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ProcessStepPostchecksResponse(ProcessStepPostchecks):
    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Paginated responses
# ---------------------------------------------------------------------------


class PaginatedProcessPostchecksResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProcessPostchecksResponse]


class PaginatedProcessStepPostchecksResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProcessStepPostchecksResponse]
