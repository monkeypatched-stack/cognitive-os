from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

BatchExecutionStatus = Literal[
    "Draft",
    "Released",
    "In Progress",
    "On Hold",
    "Completed",
    "Under QA Review",
    "Approved",
    "Rejected",
    "Cancelled",
]

BatchExecutionStepStatus = Literal[
    "Not Started",
    "In Progress",
    "Completed",
    "Skipped",
    "Failed",
    "Rejected",
]

IpcResultStatus = Literal[
    "Pending", "Pass", "Fail", "Retest Required", "Not Applicable"
]
YieldDisposition = Literal[
    "Pending", "Accepted", "Rejected", "Rework", "Investigation Required"
]
YieldExecutionStatus = Literal["Planned", "In Progress", "Executed", "Voided"]
DispenseWeighingStatus = Literal[
    "Planned",
    "In Progress",
    "Weighed",
    "Verified",
    "Rejected",
    "Cancelled",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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


class BatchExecutionStep(BaseModel):
    sequence: int = Field(..., ge=1)
    process_step_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    instruction_template_id: Optional[str] = None
    instruction_step_id: Optional[str] = None
    status: BatchExecutionStepStatus = "Not Started"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    performed_by: Optional[str] = None
    verified_by: Optional[str] = None
    actual_values: dict[str, Any] = Field(default_factory=dict)
    evidence_document_ids: list[str] = Field(default_factory=list)
    signature_ids: list[str] = Field(default_factory=list)
    comments: Optional[str] = None

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "BatchExecutionStep":
        self.started_at = ensure_utc(self.started_at)
        self.completed_at = ensure_utc(self.completed_at)
        if (
            self.completed_at
            and self.started_at
            and self.completed_at < self.started_at
        ):
            raise ValueError(
                "completed_at cannot be before started_at for a batch execution step."
            )
        return self


class IpcLimitSnapshot(BaseModel):
    lower: Optional[float] = None
    upper: Optional[float] = None
    target: Optional[float] = None
    unit: Optional[str] = None
    method: Optional[str] = None
    cqa: Optional[str] = None
    cpp: Optional[str] = None


class BatchIpcResult(BaseModel):
    checkpoint_id: str = Field(..., min_length=1)
    process_step_id: Optional[str] = None
    sample_id: Optional[str] = None
    measured_value: Optional[float] = None
    text_value: Optional[str] = None
    unit: Optional[str] = None
    status: IpcResultStatus = "Pending"
    limits_snapshot: IpcLimitSnapshot = Field(default_factory=IpcLimitSnapshot)
    sampled_by: Optional[str] = None
    tested_by: Optional[str] = None
    tested_at: Optional[datetime] = None
    evidence_document_ids: list[str] = Field(default_factory=list)
    deviation_id: Optional[str] = None

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "BatchIpcResult":
        self.tested_at = ensure_utc(self.tested_at)
        return self


class DispenseWeighingExecution(BaseModel):
    dispense_weighing_id: str = Field(..., min_length=1)
    process_step_id: str = Field(..., min_length=1)
    instruction_template_id: Optional[str] = None
    instruction_step_id: Optional[str] = None
    bmr_document_id: str = Field(..., min_length=1)
    bmr_section_id: Optional[str] = None
    bmr_line_id: Optional[str] = None

    bom_id: Optional[str] = None
    bom_component_id: Optional[str] = None
    material_id: str = Field(..., min_length=1)
    material_name: str = Field(..., min_length=1)
    material_lot_id: str = Field(..., min_length=1)
    container_id: Optional[str] = None
    ar_number: Optional[str] = None

    target_quantity: float = Field(..., gt=0)
    lower_tolerance: Optional[float] = Field(None, ge=0)
    upper_tolerance: Optional[float] = Field(None, ge=0)
    tare_weight: Optional[float] = Field(None, ge=0)
    gross_weight: Optional[float] = Field(None, ge=0)
    net_weight: Optional[float] = Field(None, ge=0)
    unit: str = Field(..., min_length=1)
    scale_equipment_id: str = Field(..., min_length=1)
    calibration_status_snapshot: Optional[str] = None
    line_clearance_id: Optional[str] = None

    status: DispenseWeighingStatus = "Planned"
    weighed_by: Optional[str] = None
    weighed_at: Optional[datetime] = None
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None
    evidence_document_ids: list[str] = Field(default_factory=list)
    signature_ids: list[str] = Field(default_factory=list)
    deviation_id: Optional[str] = None
    comments: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def within_tolerance(self) -> Optional[bool]:
        if self.net_weight is None:
            return None
        lower = self.target_quantity - (self.lower_tolerance or 0)
        upper = self.target_quantity + (self.upper_tolerance or 0)
        return lower <= self.net_weight <= upper

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "DispenseWeighingExecution":
        self.weighed_at = ensure_utc(self.weighed_at)
        self.verified_at = ensure_utc(self.verified_at)

        if (
            self.net_weight is None
            and self.gross_weight is not None
            and self.tare_weight is not None
        ):
            self.net_weight = round(self.gross_weight - self.tare_weight, 6)

        if self.net_weight is not None and self.net_weight < 0:
            raise ValueError(
                "net_weight cannot be negative for GMP dispense/weighing execution."
            )

        if self.status in ("Weighed", "Verified") and self.net_weight is None:
            raise ValueError(
                "weighed or verified dispense/weighing entries must include net_weight."
            )

        if self.status in ("Weighed", "Verified") and not self.weighed_by:
            raise ValueError(
                "weighed or verified dispense/weighing entries must include weighed_by."
            )

        if self.status == "Verified" and not self.verified_by:
            raise ValueError(
                "verified dispense/weighing entries must include verified_by."
            )

        if self.status == "Verified" and not self.signature_ids:
            raise ValueError(
                "verified dispense/weighing entries must include signature_ids."
            )

        if self.status == "Verified" and not self.evidence_document_ids:
            raise ValueError(
                "verified dispense/weighing entries must include BMR evidence_document_ids."
            )

        if self.within_tolerance is False and not self.deviation_id:
            raise ValueError(
                "out-of-tolerance dispense/weighing entries must include deviation_id."
            )

        return self


class YieldReconciliation(BaseModel):
    planned_batch_quantity: Optional[float] = Field(None, ge=0)
    theoretical_yield: Optional[float] = Field(None, ge=0)
    actual_batch_quantity: Optional[float] = Field(None, ge=0)
    actual_yield: Optional[float] = Field(None, ge=0)
    rejected_quantity: Optional[float] = Field(None, ge=0)
    scrap_quantity: Optional[float] = Field(None, ge=0)
    unit: Optional[str] = None
    variance_percent: Optional[float] = None
    execution_status: YieldExecutionStatus = "Planned"
    executed_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    disposition: YieldDisposition = "Pending"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    planned_vs_actual_comparison: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_and_compute(self) -> "YieldReconciliation":
        self.executed_at = ensure_utc(self.executed_at)
        self.reviewed_at = ensure_utc(self.reviewed_at)
        if self.actual_batch_quantity is None:
            self.actual_batch_quantity = self.actual_yield
        if self.planned_batch_quantity is None:
            self.planned_batch_quantity = self.theoretical_yield
        if self.variance_percent is None and self.theoretical_yield:
            actual = self.actual_batch_quantity or 0
            planned = self.planned_batch_quantity or self.theoretical_yield
            self.variance_percent = (
                round(((actual - planned) / planned) * 100, 4) if planned else 0
            )
        if (
            self.planned_batch_quantity is not None
            and self.actual_batch_quantity is not None
        ):
            variance_quantity = round(
                self.actual_batch_quantity - self.planned_batch_quantity, 6
            )
            self.planned_vs_actual_comparison = {
                **self.planned_vs_actual_comparison,
                "planned_batch_quantity": self.planned_batch_quantity,
                "actual_batch_quantity": self.actual_batch_quantity,
                "variance_quantity": variance_quantity,
                "variance_percent": self.variance_percent,
                "unit": self.unit,
                "comparison_result": (
                    "Within tolerance"
                    if abs(self.variance_percent or 0) <= 2
                    else "Outside tolerance"
                ),
            }
        if self.execution_status == "Executed" and (
            not self.executed_by or not self.executed_at
        ):
            raise ValueError(
                "executed embedded yield reconciliation must include executed_by and executed_at."
            )
        return self


class BatchProductionExecutionRecord(BaseModel):
    batch_execution_record_id: str = Field(..., min_length=1)
    batch_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    status: BatchExecutionStatus = "Draft"

    work_order_id: Optional[str] = None
    process_definition_id: str = Field(..., min_length=1)
    process_definition_revision: Optional[str] = None
    process_route_id: Optional[str] = None
    recipe_id: Optional[str] = None
    bom_id: Optional[str] = None
    product_id: Optional[str] = None
    lot_ids: list[str] = Field(default_factory=list)

    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    machine_ids: list[str] = Field(default_factory=list)
    equipment_ids: list[str] = Field(default_factory=list)

    instruction_template_ids: list[str] = Field(default_factory=list)
    sop_ids: list[str] = Field(default_factory=list)
    sop_document_ids: list[str] = Field(default_factory=list)

    operator_id: Optional[str] = None
    supervisor_id: Optional[str] = None
    qa_reviewer_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None

    execution_steps: list[BatchExecutionStep] = Field(default_factory=list)
    dispense_weighing_records: list[DispenseWeighingExecution] = Field(
        default_factory=list,
        description="GMP raw material dispense/weighing entries captured into the BMR for this batch.",
    )
    ipc_results: list[BatchIpcResult] = Field(default_factory=list)
    yield_reconciliation: YieldReconciliation = Field(
        default_factory=YieldReconciliation
    )

    deviation_ids: list[str] = Field(default_factory=list)
    change_control_ids: list[str] = Field(default_factory=list)
    cleaning_record_ids: list[str] = Field(
        default_factory=list,
        description="Cleaning records automatically included in the BMR/BPR batch record package.",
    )
    equipment_usage_ids: list[str] = Field(
        default_factory=list,
        description="Regulated equipment usage ledger entries automatically included in the BMR/BPR batch record package.",
    )
    signature_ids: list[str] = Field(default_factory=list)
    audit_event_ids: list[str] = Field(default_factory=list)
    evidence_document_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "BatchProductionExecutionRecord":
        self.started_at = ensure_utc(self.started_at)
        self.completed_at = ensure_utc(self.completed_at)
        self.released_at = ensure_utc(self.released_at)
        self.approved_at = ensure_utc(self.approved_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()

        if (
            self.completed_at
            and self.started_at
            and self.completed_at < self.started_at
        ):
            raise ValueError(
                "completed_at cannot be before started_at for a batch execution record."
            )

        if (
            self.status in ("Completed", "Under QA Review", "Approved")
            and not self.execution_steps
        ):
            raise ValueError(
                "completed batch execution records must include execution_steps."
            )

        if self.status == "Approved" and not self.signature_ids:
            raise ValueError(
                "approved batch execution records must include signature_ids."
            )

        has_dispensing_step = any(
            "dispens" in f"{step.process_step_id} {step.name}".lower()
            or "weigh" in f"{step.process_step_id} {step.name}".lower()
            for step in self.execution_steps
        )
        if (
            self.status in ("Completed", "Under QA Review", "Approved")
            and has_dispensing_step
            and not self.dispense_weighing_records
        ):
            raise ValueError(
                "completed batch records with dispensing/weighing steps must include dispense_weighing_records."
            )

        if self.status in ("Completed", "Under QA Review", "Approved"):
            missing_requirements, missing_records = required_cleaning_coverage(
                self.metadata, self.cleaning_record_ids
            )
            if missing_requirements:
                raise ValueError(
                    f"completed batch records missing required cleaning coverage: {', '.join(missing_requirements)}."
                )
            if missing_records:
                raise ValueError(
                    f"completed batch records reference cleaning records not attached to package: {', '.join(missing_records)}."
                )
            missing_equipment_usage = missing_required_equipment_usage(
                self.metadata, self.equipment_usage_ids
            )
            if missing_equipment_usage:
                raise ValueError(
                    f"completed batch records missing required regulated equipment usage: {', '.join(missing_equipment_usage)}."
                )

        bmr_evidence_ids = set(self.evidence_document_ids) | set(self.sop_document_ids)
        for record in self.dispense_weighing_records:
            bmr_evidence_ids.update(record.evidence_document_ids)
            if record.bmr_document_id:
                bmr_evidence_ids.add(record.bmr_document_id)
        if self.dispense_weighing_records:
            self.metadata.setdefault("bmr_package", {})
            self.metadata["bmr_package"]["dispense_weighing_record_ids"] = [
                record.dispense_weighing_id for record in self.dispense_weighing_records
            ]
            self.metadata["bmr_package"]["dispense_weighing_evidence_document_ids"] = (
                sorted(bmr_evidence_ids)
            )

        return self


class BatchProductionExecutionRecordCreate(BatchProductionExecutionRecord):
    pass


class BatchProductionExecutionRecordUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[BatchExecutionStatus] = None
    work_order_id: Optional[str] = None
    process_definition_revision: Optional[str] = None
    process_route_id: Optional[str] = None
    recipe_id: Optional[str] = None
    bom_id: Optional[str] = None
    product_id: Optional[str] = None
    lot_ids: Optional[list[str]] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    machine_ids: Optional[list[str]] = None
    equipment_ids: Optional[list[str]] = None
    instruction_template_ids: Optional[list[str]] = None
    sop_ids: Optional[list[str]] = None
    sop_document_ids: Optional[list[str]] = None
    operator_id: Optional[str] = None
    supervisor_id: Optional[str] = None
    qa_reviewer_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    execution_steps: Optional[list[BatchExecutionStep]] = None
    dispense_weighing_records: Optional[list[DispenseWeighingExecution]] = None
    ipc_results: Optional[list[BatchIpcResult]] = None
    yield_reconciliation: Optional[YieldReconciliation] = None
    deviation_ids: Optional[list[str]] = None
    change_control_ids: Optional[list[str]] = None
    cleaning_record_ids: Optional[list[str]] = None
    equipment_usage_ids: Optional[list[str]] = None
    signature_ids: Optional[list[str]] = None
    audit_event_ids: Optional[list[str]] = None
    evidence_document_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "BatchProductionExecutionRecordUpdate":
        self.started_at = ensure_utc(self.started_at)
        self.completed_at = ensure_utc(self.completed_at)
        self.released_at = ensure_utc(self.released_at)
        self.approved_at = ensure_utc(self.approved_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class BatchProductionExecutionRecordResponse(BatchProductionExecutionRecord):
    class Config:
        from_attributes = True


class PaginatedBatchProductionExecutionRecordResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[BatchProductionExecutionRecordResponse]


# Yield Reconciliation Models for separate collection
class YieldReconciliationRecord(BaseModel):
    yield_reconciliation_id: str = Field(..., min_length=1)
    batch_id: str = Field(..., min_length=1)
    batch_execution_record_id: str = Field(..., min_length=1)
    planned_batch_quantity: float = Field(..., ge=0)
    theoretical_yield: float = Field(..., ge=0)
    actual_batch_quantity: float = Field(..., ge=0)
    actual_yield: float = Field(..., ge=0)
    rejected_quantity: float = Field(..., ge=0)
    scrap_quantity: float = Field(..., ge=0)
    unit: str = Field(..., min_length=1)
    variance_percent: float
    execution_status: YieldExecutionStatus = "Planned"
    executed_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    disposition: YieldDisposition = "Pending"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    planned_vs_actual_comparison: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_compute(self) -> "YieldReconciliationRecord":
        self.executed_at = ensure_utc(self.executed_at)
        self.reviewed_at = ensure_utc(self.reviewed_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()

        if self.planned_batch_quantity is None:
            self.planned_batch_quantity = self.theoretical_yield
        if self.variance_percent is None and self.theoretical_yield:
            actual = self.actual_batch_quantity or 0
            planned = self.planned_batch_quantity or self.theoretical_yield
            self.variance_percent = (
                round(((actual - planned) / planned) * 100, 4) if planned else 0
            )
        if (
            self.planned_batch_quantity is not None
            and self.actual_batch_quantity is not None
        ):
            variance_quantity = round(
                self.actual_batch_quantity - self.planned_batch_quantity, 6
            )
            self.planned_vs_actual_comparison = {
                **self.planned_vs_actual_comparison,
                "planned_batch_quantity": self.planned_batch_quantity,
                "actual_batch_quantity": self.actual_batch_quantity,
                "variance_quantity": variance_quantity,
                "variance_percent": self.variance_percent,
                "unit": self.unit,
                "comparison_result": (
                    "Within tolerance"
                    if abs(self.variance_percent or 0) <= 2
                    else "Outside tolerance"
                ),
            }
        if self.execution_status == "Executed" and (
            not self.executed_by or not self.executed_at
        ):
            raise ValueError(
                "executed yield reconciliation must include executed_by and executed_at."
            )
        return self


class YieldReconciliationRecordCreate(YieldReconciliationRecord):
    pass


class YieldReconciliationRecordUpdate(BaseModel):
    planned_batch_quantity: Optional[float] = Field(None, ge=0)
    theoretical_yield: Optional[float] = Field(None, ge=0)
    actual_batch_quantity: Optional[float] = Field(None, ge=0)
    actual_yield: Optional[float] = Field(None, ge=0)
    rejected_quantity: Optional[float] = Field(None, ge=0)
    scrap_quantity: Optional[float] = Field(None, ge=0)
    unit: Optional[str] = None
    variance_percent: Optional[float] = None
    execution_status: Optional[YieldExecutionStatus] = None
    executed_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    disposition: Optional[YieldDisposition] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    planned_vs_actual_comparison: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "YieldReconciliationRecordUpdate":
        self.executed_at = ensure_utc(self.executed_at)
        self.reviewed_at = ensure_utc(self.reviewed_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class YieldReconciliationRecordResponse(YieldReconciliationRecord):
    class Config:
        from_attributes = True


class PaginatedYieldReconciliationRecordResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[YieldReconciliationRecordResponse]


# Dispense Weighing Execution Models
class DispenseWeighingExecutionCreate(DispenseWeighingExecution):
    pass


class DispenseWeighingExecutionUpdate(BaseModel):
    ar_number: Optional[str] = None
    bmr_document_id: Optional[str] = None
    bmr_line_id: Optional[str] = None
    bmr_section_id: Optional[str] = None
    bom_component_id: Optional[str] = None
    bom_id: Optional[str] = None
    calibration_status_snapshot: Optional[str] = None
    comments: Optional[str] = None
    container_id: Optional[str] = None
    deviation_id: Optional[str] = None
    evidence_document_ids: Optional[list[str]] = None
    gross_weight: Optional[float] = None
    instruction_step_id: Optional[str] = None
    instruction_template_id: Optional[str] = None
    line_clearance_id: Optional[str] = None
    lower_tolerance: Optional[float] = None
    material_lot_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    net_weight: Optional[float] = None
    scale_equipment_id: Optional[str] = None
    signature_ids: Optional[list[str]] = None
    status: Optional[DispenseWeighingStatus] = None
    tare_weight: Optional[float] = None
    target_quantity: Optional[float] = None
    unit: Optional[str] = None
    upper_tolerance: Optional[float] = None
    verified_at: Optional[datetime] = None
    verified_by: Optional[str] = None
    weighed_at: Optional[datetime] = None
    weighed_by: Optional[str] = None

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "DispenseWeighingExecutionUpdate":
        self.weighed_at = ensure_utc(self.weighed_at)
        self.verified_at = ensure_utc(self.verified_at)
        return self


class DispenseWeighingExecutionResponse(DispenseWeighingExecution):
    class Config:
        from_attributes = True


class PaginatedDispenseWeighingExecutionResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[DispenseWeighingExecutionResponse]
