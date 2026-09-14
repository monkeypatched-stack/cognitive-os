"""
models/sop.py
-------------
SOP (Standard Operating Procedure) — the top-level aggregate root.
Composes all process_definition sub-models plus free-form notes and remarks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator

from services.process_definitions.models.process_constraints import ProcessConstraints
from services.process_definitions.models.process_corrections import CorrectiveActions
from services.process_definitions.models.process_post_checks import ProcessPostchecks
from services.process_definitions.models.process_pre_checks import ProcessPrechecks
from services.process_definitions.models.process_definition import ProcessDefinition


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# SOP
# ---------------------------------------------------------------------------


class SOP(BaseModel):
    id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    purpose: str = Field(...)
    scope: str = Field(...)
    version: str = Field(default="1.0.0")
    effective_date: Optional[datetime] = None
    review_date: Optional[datetime] = None
    approved_by: Optional[str] = None
    authored_by: Optional[str] = None
    entity_id: Optional[str] = None
    entity_name: Optional[str] = None
    entity_type: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None

    # --- Core components ---
    process_definition: ProcessDefinition
    prechecks: ProcessPrechecks
    postchecks: ProcessPostchecks
    constraints: ProcessConstraints
    corrective_actions: CorrectiveActions

    # --- Free-form annotations ---
    notes: Optional[str] = None
    remarks: Optional[str] = None
    document_ids: List[str] = Field(default_factory=list)
    references: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "SOP":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        self.effective_date = ensure_utc(self.effective_date)
        self.review_date = ensure_utc(self.review_date)
        return self


class SOPCreate(SOP):
    pass


class SOPUpdate(BaseModel):
    title: Optional[str] = None
    purpose: Optional[str] = None
    scope: Optional[str] = None
    version: Optional[str] = None
    effective_date: Optional[datetime] = None
    review_date: Optional[datetime] = None
    approved_by: Optional[str] = None
    authored_by: Optional[str] = None
    entity_id: Optional[str] = None
    entity_name: Optional[str] = None
    entity_type: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None
    process_definition: Optional[ProcessDefinition] = None
    prechecks: Optional[ProcessPrechecks] = None
    postchecks: Optional[ProcessPostchecks] = None
    constraints: Optional[ProcessConstraints] = None
    corrective_actions: Optional[CorrectiveActions] = None
    notes: Optional[str] = None
    remarks: Optional[str] = None
    document_ids: Optional[List[str]] = None
    references: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    updated_at: datetime = Field(default_factory=utc_now)


class SOPResponse(SOP):
    class Config:
        from_attributes = True


class PaginatedSOPResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SOPResponse]


class SOPChangeImpactRequest(BaseModel):
    proposed_values: dict[str, Any] = Field(default_factory=dict)
    change_reason: str = Field(
        default="SOP revision impact analysis", min_length=1, max_length=2000
    )
    include_upstream: bool = True
    include_downstream: bool = True
    max_depth: int = Field(default=2, ge=1, le=5)
    create_change_control: bool = False
    change_type: str = Field(
        default="Major", pattern=r"^(Major|Minor|Emergency|Temporary)$"
    )
    risk_score: int | None = Field(default=45, ge=1, le=125)
    approval_chain: list[dict[str, Any]] = Field(default_factory=list)


class SOPChangeImpactResponse(BaseModel):
    source_sop: dict[str, Any]
    source_change: dict[str, Any]
    effects: dict[str, Any] = Field(default_factory=dict)
    production_connections: list[dict[str, Any]]
    linked_sops: list[dict[str, Any]]
    required_changes: list[dict[str, Any]]
    sop_graph: dict[str, Any]
    change_control: dict[str, Any] | None = None
