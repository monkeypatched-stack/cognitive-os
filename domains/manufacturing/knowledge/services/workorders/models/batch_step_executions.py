from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

BatchStepExecutionStatus = Literal[
    "Planned",
    "In Progress",
    "Completed",
    "Skipped",
    "Failed",
    "Rejected",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class BatchStepExecution(BaseModel):
    batch_step_execution_id: str = Field(..., min_length=1)
    batch_execution_record_id: str = Field(..., min_length=1)
    batch_id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    process_step_id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=1)
    step_name: str = Field(..., min_length=1)
    instruction_template_id: Optional[str] = None
    instruction_step_id: Optional[str] = None

    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    equipment_ids: list[str] = Field(default_factory=list)
    machine_ids: list[str] = Field(default_factory=list)

    operator_id: str = Field(..., min_length=1)
    operator_name: Optional[str] = None
    verifier_id: Optional[str] = None
    verifier_name: Optional[str] = None
    qa_reviewer_id: Optional[str] = None

    status: BatchStepExecutionStatus = "Planned"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_minutes: Optional[float] = Field(default=None, ge=0)
    actual_values: dict[str, Any] = Field(default_factory=dict)
    exception_notes: Optional[str] = None
    deviation_id: Optional[str] = None
    executed_instruction_evidence_ids: list[str] = Field(default_factory=list)
    signature_ids: list[str] = Field(default_factory=list)
    evidence_document_ids: list[str] = Field(default_factory=list)
    audit_event_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "BatchStepExecution":
        self.started_at = ensure_utc(self.started_at)
        self.completed_at = ensure_utc(self.completed_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()

        if (
            self.completed_at
            and self.started_at
            and self.completed_at < self.started_at
        ):
            raise ValueError(
                "completed_at cannot be before started_at for a batch step execution."
            )
        if self.duration_minutes is None and self.started_at and self.completed_at:
            self.duration_minutes = round(
                (self.completed_at - self.started_at).total_seconds() / 60, 4
            )
        if self.status == "In Progress" and not self.started_at:
            raise ValueError(
                "in-progress batch step executions must include started_at."
            )
        if self.status == "Completed" and not self.completed_at:
            raise ValueError(
                "completed batch step executions must include completed_at."
            )
        if self.status == "Completed" and not self.signature_ids:
            raise ValueError(
                "completed batch step executions must include signature_ids."
            )
        if self.status in {"Failed", "Rejected"} and not (
            self.exception_notes or self.deviation_id
        ):
            raise ValueError(
                "failed/rejected batch step executions must include exception_notes or deviation_id."
            )
        return self


class BatchStepExecutionCreate(BatchStepExecution):
    pass


class BatchStepExecutionUpdate(BaseModel):
    step_name: Optional[str] = None
    instruction_template_id: Optional[str] = None
    instruction_step_id: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    equipment_ids: Optional[list[str]] = None
    machine_ids: Optional[list[str]] = None
    operator_id: Optional[str] = None
    operator_name: Optional[str] = None
    verifier_id: Optional[str] = None
    verifier_name: Optional[str] = None
    qa_reviewer_id: Optional[str] = None
    status: Optional[BatchStepExecutionStatus] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_minutes: Optional[float] = Field(default=None, ge=0)
    actual_values: Optional[dict[str, Any]] = None
    exception_notes: Optional[str] = None
    deviation_id: Optional[str] = None
    executed_instruction_evidence_ids: Optional[list[str]] = None
    signature_ids: Optional[list[str]] = None
    evidence_document_ids: Optional[list[str]] = None
    audit_event_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "BatchStepExecutionUpdate":
        self.started_at = ensure_utc(self.started_at)
        self.completed_at = ensure_utc(self.completed_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class BatchStepExecutionResponse(BatchStepExecution):
    class Config:
        from_attributes = True


class PaginatedBatchStepExecutionResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[BatchStepExecutionResponse]
