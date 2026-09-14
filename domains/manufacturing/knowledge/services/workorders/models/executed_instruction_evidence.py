from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

ExecutedInstructionEvidenceStatus = Literal[
    "Draft", "Captured", "Verified", "Rejected", "Voided"
]
InstructionSourceRecordType = Literal[
    "BMR", "BPR", "Cleaning", "Equipment", "Area", "Other"
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ExecutedInstructionEvidence(BaseModel):
    executed_instruction_evidence_id: str = Field(..., min_length=1)
    batch_step_execution_id: Optional[str] = None
    executed_bpr_step_id: Optional[str] = None
    batch_execution_record_id: str = Field(..., min_length=1)
    batch_id: str = Field(..., min_length=1)

    instruction_template_id: str = Field(..., min_length=1)
    instruction_step_id: str = Field(..., min_length=1)
    instruction_revision: Optional[str] = None
    instruction_text_snapshot: str = Field(..., min_length=1)
    acceptance_criteria_snapshot: Optional[str] = None

    source_record_type: InstructionSourceRecordType = "BMR"
    source_record_id: Optional[str] = None
    source_document_id: Optional[str] = None
    source_section_id: Optional[str] = None

    process_definition_id: str = Field(..., min_length=1)
    process_step_id: str = Field(..., min_length=1)
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    equipment_ids: list[str] = Field(default_factory=list)

    status: ExecutedInstructionEvidenceStatus = "Draft"
    performed_by: Optional[str] = None
    performed_at: Optional[datetime] = None
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None
    actual_values: dict[str, Any] = Field(default_factory=dict)
    exception_notes: Optional[str] = None
    deviation_id: Optional[str] = None
    evidence_document_ids: list[str] = Field(default_factory=list)
    signature_ids: list[str] = Field(default_factory=list)
    audit_event_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "ExecutedInstructionEvidence":
        self.performed_at = ensure_utc(self.performed_at)
        self.verified_at = ensure_utc(self.verified_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        if not self.batch_step_execution_id and not self.executed_bpr_step_id:
            raise ValueError(
                "executed instruction evidence must include batch_step_execution_id or executed_bpr_step_id."
            )
        if (
            self.verified_at
            and self.performed_at
            and self.verified_at < self.performed_at
        ):
            raise ValueError(
                "verified_at cannot be before performed_at for executed instruction evidence."
            )
        if self.status in {"Captured", "Verified"}:
            if not self.source_record_id or not self.source_document_id:
                raise ValueError(
                    "captured executed instruction evidence must include source_record_id and source_document_id."
                )
            if not self.performed_by or not self.performed_at:
                raise ValueError(
                    "captured executed instruction evidence must include performed_by and performed_at."
                )
            if not self.actual_values:
                raise ValueError(
                    "captured executed instruction evidence must include actual_values."
                )
            if not self.evidence_document_ids:
                raise ValueError(
                    "captured executed instruction evidence must include evidence_document_ids."
                )
            if not self.signature_ids:
                raise ValueError(
                    "captured executed instruction evidence must include signature_ids."
                )
        if self.status == "Verified" and (not self.verified_by or not self.verified_at):
            raise ValueError(
                "verified executed instruction evidence must include verified_by and verified_at."
            )
        if self.status == "Rejected" and not (
            self.exception_notes or self.deviation_id
        ):
            raise ValueError(
                "rejected executed instruction evidence must include exception_notes or deviation_id."
            )
        return self


class ExecutedInstructionEvidenceCreate(ExecutedInstructionEvidence):
    pass


class ExecutedInstructionEvidenceUpdate(BaseModel):
    status: Optional[ExecutedInstructionEvidenceStatus] = None
    performed_by: Optional[str] = None
    performed_at: Optional[datetime] = None
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None
    actual_values: Optional[dict[str, Any]] = None
    exception_notes: Optional[str] = None
    deviation_id: Optional[str] = None
    evidence_document_ids: Optional[list[str]] = None
    signature_ids: Optional[list[str]] = None
    audit_event_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ExecutedInstructionEvidenceUpdate":
        self.performed_at = ensure_utc(self.performed_at)
        self.verified_at = ensure_utc(self.verified_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class ExecutedInstructionEvidenceResponse(ExecutedInstructionEvidence):
    class Config:
        from_attributes = True


class PaginatedExecutedInstructionEvidenceResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ExecutedInstructionEvidenceResponse]
