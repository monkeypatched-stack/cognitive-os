from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

ExecutedBprStatus = Literal[
    "Draft",
    "In Progress",
    "Completed",
    "Under QA Review",
    "Approved",
    "Rejected",
    "Voided",
]
ExecutedBprStepStatus = Literal[
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


class ExecutedBprStep(BaseModel):
    step_execution_id: str = Field(..., min_length=1)
    template_step_id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    process_step_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    status: ExecutedBprStepStatus = "Pending"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    performed_by: Optional[str] = None
    verified_by: Optional[str] = None
    actual_values: dict[str, Any] = Field(default_factory=dict)
    packaging_component_lot_ids: list[str] = Field(default_factory=list)
    reconciliation: dict[str, Any] = Field(default_factory=dict)
    executed_instruction_evidence_ids: list[str] = Field(default_factory=list)
    evidence_document_ids: list[str] = Field(default_factory=list)
    report_template_ids: list[str] = Field(default_factory=list)
    report_template_section_ids: list[str] = Field(default_factory=list)
    signature_ids: list[str] = Field(default_factory=list)
    comments: Optional[str] = None

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "ExecutedBprStep":
        self.started_at = ensure_utc(self.started_at)
        self.completed_at = ensure_utc(self.completed_at)
        if (
            self.completed_at
            and self.started_at
            and self.completed_at < self.started_at
        ):
            raise ValueError(
                "completed_at cannot be before started_at for an executed BPR step."
            )
        if self.status == "Completed":
            if not self.completed_at:
                raise ValueError(
                    "completed executed BPR steps must include completed_at."
                )
            if not self.performed_by:
                raise ValueError(
                    "completed executed BPR steps must include performed_by."
                )
            if not self.signature_ids:
                raise ValueError(
                    "completed executed BPR steps must include signature_ids."
                )
        return self


class ExecutedBatchPackagingRecord(BaseModel):
    executed_bpr_record_id: str = Field(..., min_length=1)
    executed_bpr_document_id: str = Field(..., min_length=1)
    batch_execution_record_id: str = Field(..., min_length=1)
    batch_id: str = Field(..., min_length=1)
    batch_number: Optional[str] = None

    packing_instruction_id: str = Field(..., min_length=1)
    instruction_template_id: str = Field(..., min_length=1)
    bpr_template_document_id: str = Field(..., min_length=1)
    template_revision: Optional[str] = None
    generated_from_approved_instruction: bool = False
    generated_at: datetime = Field(default_factory=utc_now)

    process_definition_id: str = Field(..., min_length=1)
    bpr_process_definition_id: str = Field(..., min_length=1)
    bom_id: str = Field(..., min_length=1)
    product_id: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None

    status: ExecutedBprStatus = "Draft"
    steps: list[ExecutedBprStep] = Field(default_factory=list)
    packaging_component_lot_ids: list[str] = Field(default_factory=list)
    line_clearance_ids: list[str] = Field(default_factory=list)
    serialization_lot_ids: list[str] = Field(default_factory=list)
    yield_reconciliation_record_ids: list[str] = Field(default_factory=list)
    cleaning_record_ids: list[str] = Field(default_factory=list)
    equipment_usage_ids: list[str] = Field(default_factory=list)
    area_room_usage_ids: list[str] = Field(default_factory=list)
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
    def normalize_and_validate(self) -> "ExecutedBatchPackagingRecord":
        self.generated_at = ensure_utc(self.generated_at) or utc_now()
        self.executed_at = ensure_utc(self.executed_at)
        self.reviewed_at = ensure_utc(self.reviewed_at)
        self.approved_at = ensure_utc(self.approved_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()

        if self.status in {"Completed", "Under QA Review", "Approved"}:
            if not self.generated_from_approved_instruction:
                raise ValueError(
                    "executed BPR records must be generated from an approved packing instruction."
                )
            if not self.steps:
                raise ValueError(
                    "completed executed BPR records must include executed packaging steps."
                )
            if not self.evidence_document_ids:
                raise ValueError(
                    "completed executed BPR records must include evidence_document_ids."
                )
            if not self.executed_by or not self.executed_at:
                raise ValueError(
                    "completed executed BPR records must include executed_by and executed_at."
                )
            missing_requirements, missing_records = required_cleaning_coverage(
                self.metadata, self.cleaning_record_ids
            )
            if missing_requirements:
                raise ValueError(
                    f"executed BPR records missing required cleaning coverage: {', '.join(missing_requirements)}."
                )
            if missing_records:
                raise ValueError(
                    f"executed BPR records reference cleaning records not attached to BPR package: {', '.join(missing_records)}."
                )
            missing_equipment_usage = missing_required_equipment_usage(
                self.metadata, self.equipment_usage_ids
            )
            if missing_equipment_usage:
                raise ValueError(
                    f"executed BPR records missing required regulated equipment usage: {', '.join(missing_equipment_usage)}."
                )
            incomplete = [
                step.title
                for step in self.steps
                if step.status not in {"Completed", "Skipped"}
            ]
            if incomplete:
                raise ValueError(
                    f"completed executed BPR records have incomplete steps: {', '.join(incomplete)}."
                )
            unbound_steps = [
                step.title
                for step in self.steps
                if step.status == "Completed"
                and (
                    not step.report_template_ids or not step.report_template_section_ids
                )
            ]
            if unbound_steps:
                raise ValueError(
                    f"completed executed BPR steps missing report template binding: {', '.join(unbound_steps)}."
                )
        if self.status in {"Under QA Review", "Approved"}:
            if not self.reviewed_by or not self.reviewed_at:
                raise ValueError(
                    "reviewed/approved executed BPR records must include reviewed_by and reviewed_at."
                )
        if self.status == "Approved":
            if not self.approved_by or not self.approved_at:
                raise ValueError(
                    "approved executed BPR records must include approved_by and approved_at."
                )
            if not self.signature_ids:
                raise ValueError(
                    "approved executed BPR records must include signature_ids."
                )
        return self


class ExecutedBprRecordCreate(ExecutedBatchPackagingRecord):
    pass


class ExecutedBprRecordUpdate(BaseModel):
    status: Optional[ExecutedBprStatus] = None
    steps: Optional[list[ExecutedBprStep]] = None
    packaging_component_lot_ids: Optional[list[str]] = None
    line_clearance_ids: Optional[list[str]] = None
    serialization_lot_ids: Optional[list[str]] = None
    yield_reconciliation_record_ids: Optional[list[str]] = None
    cleaning_record_ids: Optional[list[str]] = None
    equipment_usage_ids: Optional[list[str]] = None
    area_room_usage_ids: Optional[list[str]] = None
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
    def normalize_datetimes(self) -> "ExecutedBprRecordUpdate":
        self.executed_at = ensure_utc(self.executed_at)
        self.reviewed_at = ensure_utc(self.reviewed_at)
        self.approved_at = ensure_utc(self.approved_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class ExecutedBprRecordResponse(ExecutedBatchPackagingRecord):
    class Config:
        from_attributes = True


class PaginatedExecutedBprRecordResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ExecutedBprRecordResponse]
