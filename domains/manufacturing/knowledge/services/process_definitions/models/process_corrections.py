"""
models/process_corrections.py
------------------------------
Corrective action models — remediation steps triggered by failed post-checks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from services.process_definitions.models.process_definition_common import ActionType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# CorrectiveAction
# ---------------------------------------------------------------------------


class CorrectiveAction(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = Field(...)
    action_type: ActionType = Field(default=ActionType.MANUAL)
    command: Optional[str] = None
    applies_to_postchecks: List[str] = Field(default_factory=list)
    escalation_contact: Optional[str] = None
    max_attempts: int = Field(default=1, ge=1)
    expected_resolution_time_minutes: Optional[int] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "CorrectiveAction":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class CorrectiveActionCreate(CorrectiveAction):
    pass


class CorrectiveActionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    action_type: Optional[ActionType] = None
    command: Optional[str] = None
    applies_to_postchecks: Optional[List[str]] = None
    escalation_contact: Optional[str] = None
    max_attempts: Optional[int] = Field(None, ge=1)
    expected_resolution_time_minutes: Optional[int] = None
    notes: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)


class CorrectiveActionResponse(CorrectiveAction):
    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# CorrectiveActions (collection)
# ---------------------------------------------------------------------------


class CorrectiveActions(BaseModel):
    id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    process_step_id: str = Field(..., min_length=1)
    description: Optional[str] = None
    actions: List[CorrectiveAction] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "CorrectiveActions":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class CorrectiveActionsCreate(CorrectiveActions):
    pass


class CorrectiveActionsUpdate(BaseModel):
    description: Optional[str] = None
    actions: Optional[List[CorrectiveAction]] = None
    updated_at: datetime = Field(default_factory=utc_now)


class CorrectiveActionsResponse(CorrectiveActions):
    class Config:
        from_attributes = True


class PaginatedCorrectiveActionsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[CorrectiveActionsResponse]
