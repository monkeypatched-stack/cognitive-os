from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

AreaRoomUsageStatus = Literal["Planned", "In Use", "Completed", "Aborted", "Rejected"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class AreaRoomUsageLedgerEntry(BaseModel):
    usage_id: str = Field(..., min_length=1)
    batch_execution_record_id: str = Field(..., min_length=1)
    batch_id: str = Field(..., min_length=1)
    process_definition_id: Optional[str] = None
    process_step_id: str = Field(..., min_length=1)
    instruction_template_id: Optional[str] = None
    instruction_step_id: Optional[str] = None

    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    building_id: Optional[str] = None
    floor_id: Optional[str] = None
    room_id: Optional[str] = None
    bay_id: Optional[str] = None
    area_id: Optional[str] = None
    location_name: Optional[str] = None

    operator_id: str = Field(..., min_length=1)
    operator_name: Optional[str] = None
    supervisor_id: Optional[str] = None
    qa_reviewer_id: Optional[str] = None

    status: AreaRoomUsageStatus = "Planned"
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_minutes: Optional[float] = Field(default=None, ge=0)
    intended_use: Optional[str] = None

    regulated: bool = True
    gxp_impact: bool = True
    room_clearance_status_snapshot: Optional[str] = None
    cleaning_status_snapshot: Optional[str] = None
    environmental_status_snapshot: Optional[str] = None
    pressure_status_snapshot: Optional[str] = None
    temperature_c_snapshot: Optional[float] = None
    humidity_pct_snapshot: Optional[float] = Field(default=None, ge=0, le=100)
    pre_use_checklist_id: Optional[str] = None
    post_use_checklist_id: Optional[str] = None
    signature_ids: list[str] = Field(default_factory=list)
    audit_event_ids: list[str] = Field(default_factory=list)
    evidence_document_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "AreaRoomUsageLedgerEntry":
        self.started_at = ensure_utc(self.started_at) or utc_now()
        self.ended_at = ensure_utc(self.ended_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()

        if not (self.room_id or self.area_id or self.bay_id):
            raise ValueError(
                "area/room usage ledger entries must reference a room_id, area_id, or bay_id."
            )
        if self.ended_at and self.ended_at < self.started_at:
            raise ValueError(
                "ended_at cannot be before started_at for an area/room usage ledger entry."
            )
        if self.duration_minutes is None and self.ended_at:
            elapsed = (self.ended_at - self.started_at).total_seconds() / 60
            self.duration_minutes = round(max(elapsed, 0), 4)
        if self.status == "Completed" and not self.ended_at:
            raise ValueError(
                "completed area/room usage ledger entries must include ended_at."
            )
        if self.regulated and self.status == "Completed" and not self.signature_ids:
            raise ValueError(
                "completed regulated area/room usage entries must include signature_ids."
            )
        return self


class AreaRoomUsageLedgerCreate(AreaRoomUsageLedgerEntry):
    pass


class AreaRoomUsageLedgerUpdate(BaseModel):
    process_definition_id: Optional[str] = None
    process_step_id: Optional[str] = None
    instruction_template_id: Optional[str] = None
    instruction_step_id: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    building_id: Optional[str] = None
    floor_id: Optional[str] = None
    room_id: Optional[str] = None
    bay_id: Optional[str] = None
    area_id: Optional[str] = None
    location_name: Optional[str] = None
    operator_id: Optional[str] = None
    operator_name: Optional[str] = None
    supervisor_id: Optional[str] = None
    qa_reviewer_id: Optional[str] = None
    status: Optional[AreaRoomUsageStatus] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_minutes: Optional[float] = Field(default=None, ge=0)
    intended_use: Optional[str] = None
    regulated: Optional[bool] = None
    gxp_impact: Optional[bool] = None
    room_clearance_status_snapshot: Optional[str] = None
    cleaning_status_snapshot: Optional[str] = None
    environmental_status_snapshot: Optional[str] = None
    pressure_status_snapshot: Optional[str] = None
    temperature_c_snapshot: Optional[float] = None
    humidity_pct_snapshot: Optional[float] = Field(default=None, ge=0, le=100)
    pre_use_checklist_id: Optional[str] = None
    post_use_checklist_id: Optional[str] = None
    signature_ids: Optional[list[str]] = None
    audit_event_ids: Optional[list[str]] = None
    evidence_document_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "AreaRoomUsageLedgerUpdate":
        self.started_at = ensure_utc(self.started_at)
        self.ended_at = ensure_utc(self.ended_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class AreaRoomUsageLedgerResponse(AreaRoomUsageLedgerEntry):
    class Config:
        from_attributes = True


class PaginatedAreaRoomUsageLedgerResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[AreaRoomUsageLedgerResponse]
