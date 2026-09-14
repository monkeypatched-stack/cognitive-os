from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

ProductionBatchStatus = Literal[
    "Planned",
    "Created",
    "Released",
    "Dispensing",
    "In Production",
    "On Hold",
    "Completed",
    "Under QA Review",
    "Approved",
    "Rejected",
    "Cancelled",
    "Closed",
]

BatchLifecycleEventType = Literal[
    "Created",
    "Released",
    "Started",
    "Step Started",
    "Step Completed",
    "Hold Placed",
    "Hold Released",
    "Completed",
    "QA Review Started",
    "Approved",
    "Rejected",
    "Closed",
    "Cancelled",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class BatchLifecycleEvent(BaseModel):
    event_id: str = Field(..., min_length=1)
    event_type: BatchLifecycleEventType
    status_from: Optional[ProductionBatchStatus] = None
    status_to: ProductionBatchStatus
    occurred_at: datetime = Field(default_factory=utc_now)
    performed_by: Optional[str] = None
    reason: Optional[str] = None
    batch_execution_record_id: Optional[str] = None
    process_step_id: Optional[str] = None
    evidence_document_ids: list[str] = Field(default_factory=list)
    signature_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "BatchLifecycleEvent":
        self.occurred_at = ensure_utc(self.occurred_at) or utc_now()
        return self


class ProductionBatch(BaseModel):
    batch_id: str = Field(..., min_length=1)
    batch_number: str = Field(..., min_length=1)
    product_id: str = Field(..., min_length=1)
    bom_id: Optional[str] = None
    process_definition_id: Optional[str] = None
    process_route_id: Optional[str] = None
    work_order_id: Optional[str] = None

    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    current_stage_id: Optional[str] = None
    current_workstation_id: Optional[str] = None
    lot_ids: list[str] = Field(default_factory=list)

    status: ProductionBatchStatus = "Created"
    lifecycle_events: list[BatchLifecycleEvent] = Field(default_factory=list)
    batch_execution_record_ids: list[str] = Field(default_factory=list)
    active_batch_execution_record_id: Optional[str] = None

    planned_start_at: Optional[datetime] = None
    planned_end_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    released_by: Optional[str] = None
    qa_reviewer_id: Optional[str] = None
    created_by: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "ProductionBatch":
        self.planned_start_at = ensure_utc(self.planned_start_at)
        self.planned_end_at = ensure_utc(self.planned_end_at)
        self.released_at = ensure_utc(self.released_at)
        self.started_at = ensure_utc(self.started_at)
        self.completed_at = ensure_utc(self.completed_at)
        self.closed_at = ensure_utc(self.closed_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()

        if (
            self.planned_start_at
            and self.planned_end_at
            and self.planned_end_at < self.planned_start_at
        ):
            raise ValueError("planned_end_at cannot be before planned_start_at.")
        if (
            self.completed_at
            and self.started_at
            and self.completed_at < self.started_at
        ):
            raise ValueError("completed_at cannot be before started_at.")
        if self.closed_at and self.completed_at and self.closed_at < self.completed_at:
            raise ValueError("closed_at cannot be before completed_at.")
        if (
            self.status
            in {
                "Released",
                "Dispensing",
                "In Production",
                "Completed",
                "Under QA Review",
                "Approved",
                "Closed",
            }
            and not self.released_at
        ):
            raise ValueError("released production batches must include released_at.")
        if self.status in {"Approved", "Closed"} and not self.qa_reviewer_id:
            raise ValueError(
                "approved or closed production batches must include qa_reviewer_id."
            )
        return self


class ProductionBatchCreate(ProductionBatch):
    pass


class ProductionBatchUpdate(BaseModel):
    batch_number: Optional[str] = None
    product_id: Optional[str] = None
    bom_id: Optional[str] = None
    process_definition_id: Optional[str] = None
    process_route_id: Optional[str] = None
    work_order_id: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    current_stage_id: Optional[str] = None
    current_workstation_id: Optional[str] = None
    lot_ids: Optional[list[str]] = None
    status: Optional[ProductionBatchStatus] = None
    lifecycle_events: Optional[list[BatchLifecycleEvent]] = None
    batch_execution_record_ids: Optional[list[str]] = None
    active_batch_execution_record_id: Optional[str] = None
    planned_start_at: Optional[datetime] = None
    planned_end_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    released_by: Optional[str] = None
    qa_reviewer_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProductionBatchUpdate":
        self.planned_start_at = ensure_utc(self.planned_start_at)
        self.planned_end_at = ensure_utc(self.planned_end_at)
        self.released_at = ensure_utc(self.released_at)
        self.started_at = ensure_utc(self.started_at)
        self.completed_at = ensure_utc(self.completed_at)
        self.closed_at = ensure_utc(self.closed_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class ProductionBatchResponse(ProductionBatch):
    class Config:
        from_attributes = True


class PaginatedProductionBatchResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProductionBatchResponse]
