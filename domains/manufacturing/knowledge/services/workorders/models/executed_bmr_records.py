from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

ExecutedBmrStatus = Literal[
    "Draft",
    "In Progress",
    "Completed",
    "Under QA Review",
    "Approved",
    "Rejected",
    "Voided",
]
ExecutedBmrSectionStatus = Literal[
    "Pending", "In Progress", "Completed", "Skipped", "Rejected"
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def required_cleaning_coverage(
    metadata: dict[str, Any], cleaning_record_ids: list[str]
) -> tuple[list[str], list[str]]:
    required = [
        str(item)
        for item in metadata.get("required_cleaning_requirement_ids") or []
        if item
    ]
    coverage = (
        metadata.get("cleaning_requirement_record_map")
        if isinstance(metadata.get("cleaning_requirement_record_map"), dict)
        else {}
    )
    missing = [
        requirement_id
        for requirement_id in required
        if not coverage.get(requirement_id)
    ]
    referenced_records = {str(value) for value in coverage.values() if value}
    missing_records = sorted(referenced_records.difference(set(cleaning_record_ids)))
    return missing, missing_records


def missing_required_equipment_usage(
    metadata: dict[str, Any], equipment_usage_ids: list[str]
) -> list[str]:
    required = {
        str(item) for item in metadata.get("required_equipment_usage_ids") or [] if item
    }
    attached = {str(item) for item in equipment_usage_ids if item}
    return sorted(required.difference(attached))


class ExecutedBmrSection(BaseModel):
    section_execution_id: str = Field(..., min_length=1)
    template_section_id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    process_step_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    batch_step_execution_id: Optional[str] = None
    status: ExecutedBmrSectionStatus = "Pending"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    performed_by: Optional[str] = None
    verified_by: Optional[str] = None
    actual_values: dict[str, Any] = Field(default_factory=dict)
    executed_instruction_evidence_ids: list[str] = Field(default_factory=list)
    evidence_document_ids: list[str] = Field(default_factory=list)
    report_template_ids: list[str] = Field(default_factory=list)
    report_template_section_ids: list[str] = Field(default_factory=list)
    signature_ids: list[str] = Field(default_factory=list)
    comments: Optional[str] = None

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "ExecutedBmrSection":
        self.started_at = ensure_utc(self.started_at)
        self.completed_at = ensure_utc(self.completed_at)
        if (
            self.completed_at
            and self.started_at
            and self.completed_at < self.started_at
        ):
            raise ValueError(
                "completed_at cannot be before started_at for an executed BMR section."
            )
        if self.status == "Completed":
            if not self.completed_at:
                raise ValueError(
                    "completed executed BMR sections must include completed_at."
                )
            if not self.performed_by:
                raise ValueError(
                    "completed executed BMR sections must include performed_by."
                )
            if not self.signature_ids:
                raise ValueError(
                    "completed executed BMR sections must include signature_ids."
                )
        return self


class ExecutedBatchManufacturingRecord(BaseModel):
    executed_bmr_record_id: str = Field(..., min_length=1)
    executed_bmr_document_id: str = Field(..., min_length=1)
    batch_execution_record_id: str = Field(..., min_length=1)
    batch_id: str = Field(..., min_length=1)
    batch_number: Optional[str] = None

    mbmr_template_id: str = Field(..., min_length=1)
    mbmr_document_id: str = Field(..., min_length=1)
    bmr_template_id: str = Field(..., min_length=1)
    bmr_template_document_id: str = Field(..., min_length=1)
    template_revision: Optional[str] = None
    generated_from_approved_template: bool = False
    generated_at: datetime = Field(default_factory=utc_now)

    process_definition_id: str = Field(..., min_length=1)
    process_route_id: Optional[str] = None
    bom_id: str = Field(..., min_length=1)
    product_id: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None

    status: ExecutedBmrStatus = "Draft"
    sections: list[ExecutedBmrSection] = Field(default_factory=list)
    batch_step_execution_ids: list[str] = Field(default_factory=list)
    dispense_weighing_record_ids: list[str] = Field(default_factory=list)
    cleaning_record_ids: list[str] = Field(default_factory=list)
    equipment_usage_ids: list[str] = Field(default_factory=list)
    area_room_usage_ids: list[str] = Field(default_factory=list)
    yield_reconciliation_record_ids: list[str] = Field(default_factory=list)
    cpp_cqa_registry_ids: list[str] = Field(default_factory=list)
    deviation_ids: list[str] = Field(default_factory=list)
    change_control_ids: list[str] = Field(default_factory=list)
    evidence_document_ids: list[str] = Field(default_factory=list)

    executed_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    signature_ids: list[str] = Field(default_factory=list)
    audit_event_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "ExecutedBatchManufacturingRecord":
        self.generated_at = ensure_utc(self.generated_at) or utc_now()
        self.executed_at = ensure_utc(self.executed_at)
        self.reviewed_at = ensure_utc(self.reviewed_at)
        self.approved_at = ensure_utc(self.approved_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()

        if self.status in {"Completed", "Under QA Review", "Approved"}:
            if not self.generated_from_approved_template:
                raise ValueError(
                    "executed BMR records must be generated from an approved MBMR/BMR template."
                )
            if not self.sections:
                raise ValueError(
                    "completed executed BMR records must include executed sections."
                )
            if not self.batch_step_execution_ids:
                raise ValueError(
                    "completed executed BMR records must include batch_step_execution_ids."
                )
            if not self.evidence_document_ids:
                raise ValueError(
                    "completed executed BMR records must include evidence_document_ids."
                )
            if not self.executed_by or not self.executed_at:
                raise ValueError(
                    "completed executed BMR records must include executed_by and executed_at."
                )
            missing_requirements, missing_records = required_cleaning_coverage(
                self.metadata, self.cleaning_record_ids
            )
            if missing_requirements:
                raise ValueError(
                    f"executed BMR records missing required cleaning coverage: {', '.join(missing_requirements)}."
                )
            if missing_records:
                raise ValueError(
                    f"executed BMR records reference cleaning records not attached to BMR package: {', '.join(missing_records)}."
                )
            missing_equipment_usage = missing_required_equipment_usage(
                self.metadata, self.equipment_usage_ids
            )
            if missing_equipment_usage:
                raise ValueError(
                    f"executed BMR records missing required regulated equipment usage: {', '.join(missing_equipment_usage)}."
                )
            incomplete = [
                section.title
                for section in self.sections
                if section.status not in {"Completed", "Skipped"}
            ]
            if incomplete:
                raise ValueError(
                    f"completed executed BMR records have incomplete sections: {', '.join(incomplete)}."
                )
            unbound_sections = [
                section.title
                for section in self.sections
                if section.status == "Completed"
                and (
                    not section.report_template_ids
                    or not section.report_template_section_ids
                )
            ]
            if unbound_sections:
                raise ValueError(
                    f"completed executed BMR sections missing report template binding: {', '.join(unbound_sections)}."
                )
        if self.status in {"Under QA Review", "Approved"}:
            if not self.reviewed_by or not self.reviewed_at:
                raise ValueError(
                    "reviewed/approved executed BMR records must include reviewed_by and reviewed_at."
                )
        if self.status == "Approved":
            if not self.approved_by or not self.approved_at:
                raise ValueError(
                    "approved executed BMR records must include approved_by and approved_at."
                )
            if not self.signature_ids:
                raise ValueError(
                    "approved executed BMR records must include signature_ids."
                )
        return self


class ExecutedBmrRecordCreate(ExecutedBatchManufacturingRecord):
    pass


class ExecutedBmrRecordUpdate(BaseModel):
    status: Optional[ExecutedBmrStatus] = None
    sections: Optional[list[ExecutedBmrSection]] = None
    batch_step_execution_ids: Optional[list[str]] = None
    dispense_weighing_record_ids: Optional[list[str]] = None
    cleaning_record_ids: Optional[list[str]] = None
    equipment_usage_ids: Optional[list[str]] = None
    area_room_usage_ids: Optional[list[str]] = None
    yield_reconciliation_record_ids: Optional[list[str]] = None
    cpp_cqa_registry_ids: Optional[list[str]] = None
    deviation_ids: Optional[list[str]] = None
    change_control_ids: Optional[list[str]] = None
    evidence_document_ids: Optional[list[str]] = None
    executed_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    signature_ids: Optional[list[str]] = None
    audit_event_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ExecutedBmrRecordUpdate":
        self.executed_at = ensure_utc(self.executed_at)
        self.reviewed_at = ensure_utc(self.reviewed_at)
        self.approved_at = ensure_utc(self.approved_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class ExecutedBmrRecordResponse(ExecutedBatchManufacturingRecord):
    class Config:
        from_attributes = True


class PaginatedExecutedBmrRecordResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ExecutedBmrRecordResponse]
