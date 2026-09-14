from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class PackingInstructionStatus(str, Enum):
    DRAFT = "Draft"
    IN_REVIEW = "In Review"
    APPROVED = "Approved"
    EFFECTIVE = "Effective"
    RETIRED = "Retired"
    REJECTED = "Rejected"


class PackingInstructionApprovalStatus(str, Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    NOT_REQUIRED = "Not Required"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PackingInstructionStep(BaseModel):
    sequence: int = Field(..., ge=1)
    process_step_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    instruction: str = Field(..., min_length=1)
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    evidence_required: bool = True
    signature_required: bool = True
    ipc_checkpoint_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PackingInstructionApprovalStep(BaseModel):
    sequence: int = Field(..., ge=1)
    stage: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    assigned_user_id: str = Field(..., min_length=1)
    assigned_user_name: str = Field(..., min_length=1)
    assigned_user_email: Optional[str] = None
    status: PackingInstructionApprovalStatus = PackingInstructionApprovalStatus.PENDING
    approved_at: Optional[datetime] = None
    signature_id: Optional[str] = None
    comments: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "PackingInstructionApprovalStep":
        self.approved_at = ensure_utc(self.approved_at)
        if (
            self.status == PackingInstructionApprovalStatus.APPROVED
            and not self.signature_id
        ):
            raise ValueError(
                "approved packing instruction approval steps must include signature_id."
            )
        return self

    model_config = {"use_enum_values": True}


class PackingInstruction(BaseModel):
    packing_instruction_id: str = Field(..., min_length=1)
    instruction_template_id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    bpr_process_definition_id: str = Field(..., min_length=1)
    bpr_document_id: str = Field(..., min_length=1)
    product_id: Optional[str] = None
    bom_id: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None

    title: str = Field(..., min_length=1)
    version: str = Field(default="1.0.0")
    revision: str = Field(default="A")
    status: PackingInstructionStatus = PackingInstructionStatus.DRAFT
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    steps: list[PackingInstructionStep] = Field(default_factory=list)
    approval_chain: list[PackingInstructionApprovalStep] = Field(default_factory=list)
    approval_chain_resolved: bool = False
    approval_chain_resolved_at: Optional[datetime] = None
    signature_ids: list[str] = Field(default_factory=list)
    evidence_document_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "PackingInstruction":
        self.effective_from = ensure_utc(self.effective_from)
        self.effective_to = ensure_utc(self.effective_to)
        self.approved_at = ensure_utc(self.approved_at)
        self.approval_chain_resolved_at = ensure_utc(self.approval_chain_resolved_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()

        if (
            self.effective_to
            and self.effective_from
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to cannot be before effective_from.")
        if self.status in {
            PackingInstructionStatus.APPROVED,
            PackingInstructionStatus.EFFECTIVE,
        }:
            if not self.approved_by or not self.approved_at:
                raise ValueError(
                    "approved/effective packing instructions must include approved_by and approved_at."
                )
            if not self.steps:
                raise ValueError(
                    "approved/effective packing instructions must include steps."
                )
            if not self.approval_chain:
                raise ValueError(
                    "approved/effective packing instructions must include a named approval_chain."
                )
            if not self.approval_chain_resolved:
                raise ValueError(
                    "approved/effective packing instructions must have approval_chain_resolved set."
                )
            unresolved = [
                step.stage
                for step in self.approval_chain
                if step.status != PackingInstructionApprovalStatus.APPROVED
            ]
            if unresolved:
                raise ValueError(
                    f"approved/effective packing instructions have unresolved approvals: {', '.join(unresolved)}."
                )
            if not self.signature_ids:
                raise ValueError(
                    "approved/effective packing instructions must include signature_ids."
                )
        return self

    model_config = {"use_enum_values": True}


class PackingInstructionCreate(PackingInstruction):
    pass


class PackingInstructionUpdate(BaseModel):
    title: Optional[str] = None
    version: Optional[str] = None
    revision: Optional[str] = None
    status: Optional[PackingInstructionStatus] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    steps: Optional[list[PackingInstructionStep]] = None
    approval_chain: Optional[list[PackingInstructionApprovalStep]] = None
    approval_chain_resolved: Optional[bool] = None
    approval_chain_resolved_at: Optional[datetime] = None
    signature_ids: Optional[list[str]] = None
    evidence_document_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "PackingInstructionUpdate":
        self.effective_from = ensure_utc(self.effective_from)
        self.effective_to = ensure_utc(self.effective_to)
        self.approved_at = ensure_utc(self.approved_at)
        self.approval_chain_resolved_at = ensure_utc(self.approval_chain_resolved_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class PackingInstructionResponse(PackingInstruction):
    class Config:
        from_attributes = True


class PaginatedPackingInstructionResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[PackingInstructionResponse]
