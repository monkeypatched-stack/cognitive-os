from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class MbmrTemplateStatus(str, Enum):
    DRAFT = "Draft"
    IN_REVIEW = "In Review"
    APPROVED = "Approved"
    EFFECTIVE = "Effective"
    RETIRED = "Retired"
    REJECTED = "Rejected"


class MbmrApprovalStatus(str, Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MbmrTemplateSection(BaseModel):
    section_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=1)
    process_step_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    instruction_template_id: Optional[str] = None
    required_evidence_document_ids: list[str] = Field(default_factory=list)
    signature_required: bool = True
    content: str = Field(..., min_length=1)
    acceptance_criteria: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MbmrTemplateApprovalStep(BaseModel):
    sequence: int = Field(..., ge=1)
    stage: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    assigned_user_id: str = Field(..., min_length=1)
    assigned_user_name: str = Field(..., min_length=1)
    assigned_user_email: Optional[str] = None
    status: MbmrApprovalStatus = MbmrApprovalStatus.PENDING
    approved_at: Optional[datetime] = None
    signature_id: Optional[str] = None
    comments: Optional[str] = None

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "MbmrTemplateApprovalStep":
        self.approved_at = ensure_utc(self.approved_at)
        if self.status == MbmrApprovalStatus.APPROVED and not self.signature_id:
            raise ValueError("approved MBMR approval steps must include signature_id.")
        return self

    model_config = {"use_enum_values": True}


class MasterBatchManufacturingRecordTemplate(BaseModel):
    mbmr_template_id: str = Field(..., min_length=1)
    document_id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    process_route_id: Optional[str] = None
    product_id: str = Field(..., min_length=1)
    bom_id: str = Field(..., min_length=1)
    plant_id: Optional[str] = None
    line_id: Optional[str] = None

    title: str = Field(..., min_length=1)
    version: str = "1.0.0"
    revision: str = "A"
    status: MbmrTemplateStatus = MbmrTemplateStatus.DRAFT
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    bmr_template_id: Optional[str] = None
    bmr_document_id: Optional[str] = None
    sections: list[MbmrTemplateSection] = Field(default_factory=list)
    approval_chain: list[MbmrTemplateApprovalStep] = Field(default_factory=list)
    approval_chain_resolved: bool = False
    approval_chain_resolved_at: Optional[datetime] = None
    signature_ids: list[str] = Field(default_factory=list)
    evidence_document_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "MasterBatchManufacturingRecordTemplate":
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
        if self.status in {MbmrTemplateStatus.APPROVED, MbmrTemplateStatus.EFFECTIVE}:
            if not self.approved_by or not self.approved_at:
                raise ValueError(
                    "approved/effective MBMR templates must include approved_by and approved_at."
                )
            if not self.sections:
                raise ValueError(
                    "approved/effective MBMR templates must include sections."
                )
            if not self.approval_chain or not self.approval_chain_resolved:
                raise ValueError(
                    "approved/effective MBMR templates must include a resolved named approval_chain."
                )
            unresolved = [
                step.stage
                for step in self.approval_chain
                if step.status != MbmrApprovalStatus.APPROVED
            ]
            if unresolved:
                raise ValueError(
                    f"approved/effective MBMR templates have unresolved approvals: {', '.join(unresolved)}."
                )
            if not self.signature_ids:
                raise ValueError(
                    "approved/effective MBMR templates must include signature_ids."
                )
        return self

    model_config = {"use_enum_values": True}


class MbmrTemplateCreate(MasterBatchManufacturingRecordTemplate):
    pass


class MbmrTemplateUpdate(BaseModel):
    title: Optional[str] = None
    version: Optional[str] = None
    revision: Optional[str] = None
    status: Optional[MbmrTemplateStatus] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    sections: Optional[list[MbmrTemplateSection]] = None
    approval_chain: Optional[list[MbmrTemplateApprovalStep]] = None
    approval_chain_resolved: Optional[bool] = None
    approval_chain_resolved_at: Optional[datetime] = None
    signature_ids: Optional[list[str]] = None
    evidence_document_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "MbmrTemplateUpdate":
        self.effective_from = ensure_utc(self.effective_from)
        self.effective_to = ensure_utc(self.effective_to)
        self.approved_at = ensure_utc(self.approved_at)
        self.approval_chain_resolved_at = ensure_utc(self.approval_chain_resolved_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class MbmrTemplateResponse(MasterBatchManufacturingRecordTemplate):
    class Config:
        from_attributes = True


class PaginatedMbmrTemplateResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[MbmrTemplateResponse]
