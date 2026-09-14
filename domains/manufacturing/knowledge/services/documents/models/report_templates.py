from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

ReportTemplateType = Literal["MBMR", "BMR", "BPR", "Yield", "IPC", "Log"]
ReportTemplateStatus = Literal["Draft", "In Review", "Approved", "Effective", "Retired"]
ReportOutputFormat = Literal["PDF", "HTML", "CSV", "JSON"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ReportTemplateSection(BaseModel):
    section_id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    source_collection: str = Field(..., min_length=1)
    source_fields: list[str] = Field(default_factory=list)
    required: bool = True
    signature_required: bool = False
    reviewer_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReportTemplate(BaseModel):
    report_template_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    template_type: ReportTemplateType
    version: str = Field(..., min_length=1)
    revision: str = Field(default="A", min_length=1)
    status: ReportTemplateStatus = "Draft"
    description: Optional[str] = None

    document_template_id: Optional[str] = None
    process_definition_id: Optional[str] = None
    batch_execution_record_id: Optional[str] = None
    source_collections: list[str] = Field(default_factory=list)
    required_record_ids: list[str] = Field(default_factory=list)
    output_formats: list[ReportOutputFormat] = Field(default_factory=lambda: ["PDF"])
    sections: list[ReportTemplateSection] = Field(default_factory=list)

    retention_period: str = "10 years"
    gxp_controlled: bool = True
    part11_signature_required: bool = True
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    effective_at: Optional[datetime] = None
    signature_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "ReportTemplate":
        self.approved_at = ensure_utc(self.approved_at)
        self.effective_at = ensure_utc(self.effective_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()

        if not self.sections:
            raise ValueError("report templates must include at least one section.")
        if not self.source_collections:
            self.source_collections = sorted(
                {section.source_collection for section in self.sections}
            )
        if self.status in {"Approved", "Effective"}:
            if not self.approved_by or not self.approved_at:
                raise ValueError(
                    "approved/effective report templates must include approved_by and approved_at."
                )
            if self.part11_signature_required and not self.signature_ids:
                raise ValueError(
                    "approved/effective Part 11 report templates must include signature_ids."
                )
        if self.status == "Effective" and not self.effective_at:
            self.effective_at = self.approved_at or utc_now()
        return self


class ReportTemplateCreate(ReportTemplate):
    pass


class ReportTemplateUpdate(BaseModel):
    name: Optional[str] = None
    template_type: Optional[ReportTemplateType] = None
    version: Optional[str] = None
    revision: Optional[str] = None
    status: Optional[ReportTemplateStatus] = None
    description: Optional[str] = None
    document_template_id: Optional[str] = None
    process_definition_id: Optional[str] = None
    batch_execution_record_id: Optional[str] = None
    source_collections: Optional[list[str]] = None
    required_record_ids: Optional[list[str]] = None
    output_formats: Optional[list[ReportOutputFormat]] = None
    sections: Optional[list[ReportTemplateSection]] = None
    retention_period: Optional[str] = None
    gxp_controlled: Optional[bool] = None
    part11_signature_required: Optional[bool] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    effective_at: Optional[datetime] = None
    signature_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ReportTemplateUpdate":
        self.approved_at = ensure_utc(self.approved_at)
        self.effective_at = ensure_utc(self.effective_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class ReportTemplateResponse(ReportTemplate):
    class Config:
        from_attributes = True


class PaginatedReportTemplateResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ReportTemplateResponse]
