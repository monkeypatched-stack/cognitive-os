from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

YieldReconciliationStatus = Literal[
    "Draft", "Calculated", "Reviewed", "Approved", "Rejected"
]
YieldDisposition = Literal[
    "Pending", "Accepted", "Rejected", "Rework", "Investigation Required"
]
YieldExecutionStatus = Literal["Planned", "In Progress", "Executed", "Voided"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class YieldReconciliationRecord(BaseModel):
    yield_reconciliation_id: str = Field(..., min_length=1)
    batch_execution_record_id: str = Field(..., min_length=1)
    batch_id: str = Field(..., min_length=1)
    process_definition_id: str = Field(..., min_length=1)
    product_id: Optional[str] = None
    bom_id: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None

    planned_quantity: float = Field(..., ge=0)
    theoretical_yield: float = Field(..., ge=0)
    actual_yield: float = Field(..., ge=0)
    planned_batch_quantity: Optional[float] = Field(default=None, ge=0)
    actual_batch_quantity: Optional[float] = Field(default=None, ge=0)
    rejected_quantity: float = Field(default=0, ge=0)
    scrap_quantity: float = Field(default=0, ge=0)
    rework_quantity: float = Field(default=0, ge=0)
    unit: str = Field(..., min_length=1)
    yield_percent: Optional[float] = None
    variance_quantity: Optional[float] = None
    variance_percent: Optional[float] = None

    execution_status: YieldExecutionStatus = "Planned"
    executed_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    status: YieldReconciliationStatus = "Draft"
    disposition: YieldDisposition = "Pending"
    calculated_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    deviation_id: Optional[str] = None
    comments: Optional[str] = None
    signature_ids: list[str] = Field(default_factory=list)
    evidence_document_ids: list[str] = Field(default_factory=list)
    planned_vs_actual_comparison: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_compute(self) -> "YieldReconciliationRecord":
        self.calculated_at = ensure_utc(self.calculated_at)
        self.executed_at = ensure_utc(self.executed_at)
        self.reviewed_at = ensure_utc(self.reviewed_at)
        self.approved_at = ensure_utc(self.approved_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()

        if self.planned_batch_quantity is None:
            self.planned_batch_quantity = self.planned_quantity
        if self.actual_batch_quantity is None:
            self.actual_batch_quantity = self.actual_yield
        if self.yield_percent is None and self.theoretical_yield:
            self.yield_percent = round(
                (self.actual_yield / self.theoretical_yield) * 100, 4
            )
        if self.variance_quantity is None:
            self.variance_quantity = round(
                self.actual_batch_quantity - self.planned_batch_quantity, 6
            )
        if self.variance_percent is None and self.theoretical_yield:
            self.variance_percent = (
                round((self.variance_quantity / self.planned_batch_quantity) * 100, 4)
                if self.planned_batch_quantity
                else 0
            )
        self.planned_vs_actual_comparison = {
            **self.planned_vs_actual_comparison,
            "planned_batch_quantity": self.planned_batch_quantity,
            "actual_batch_quantity": self.actual_batch_quantity,
            "variance_quantity": self.variance_quantity,
            "variance_percent": self.variance_percent,
            "unit": self.unit,
            "comparison_result": (
                "Within tolerance"
                if abs(self.variance_percent or 0) <= 2
                else "Outside tolerance"
            ),
        }

        if self.execution_status == "Executed":
            if self.actual_batch_quantity != self.actual_yield:
                raise ValueError(
                    "actual_batch_quantity must match actual_yield for executed yield reconciliation records."
                )
            if self.planned_batch_quantity != self.planned_quantity:
                raise ValueError(
                    "planned_batch_quantity must match planned_quantity for executed yield reconciliation records."
                )

        total_accounted = (
            self.actual_yield
            + self.rejected_quantity
            + self.scrap_quantity
            + self.rework_quantity
        )
        if total_accounted > self.planned_quantity and self.status in {
            "Reviewed",
            "Approved",
        }:
            raise ValueError(
                "accounted yield quantities cannot exceed planned_quantity for reviewed/approved records."
            )
        if self.execution_status == "Executed" and (
            not self.executed_by or not self.executed_at
        ):
            raise ValueError(
                "executed yield reconciliation records must include executed_by and executed_at."
            )
        if (
            self.status in {"Reviewed", "Approved"}
            and self.execution_status != "Executed"
        ):
            raise ValueError(
                "reviewed/approved yield reconciliation records must have execution_status 'Executed'."
            )
        if self.status in {"Reviewed", "Approved"} and not self.reviewed_by:
            raise ValueError(
                "reviewed/approved yield reconciliation records must include reviewed_by."
            )
        if self.status == "Approved" and not self.approved_by:
            raise ValueError(
                "approved yield reconciliation records must include approved_by."
            )
        if self.status == "Approved" and not self.signature_ids:
            raise ValueError(
                "approved yield reconciliation records must include signature_ids."
            )
        if self.disposition == "Investigation Required" and not self.deviation_id:
            raise ValueError(
                "yield reconciliations requiring investigation must include deviation_id."
            )
        return self


class YieldReconciliationRecordCreate(YieldReconciliationRecord):
    pass


class YieldReconciliationRecordUpdate(BaseModel):
    planned_quantity: Optional[float] = Field(default=None, ge=0)
    theoretical_yield: Optional[float] = Field(default=None, ge=0)
    actual_yield: Optional[float] = Field(default=None, ge=0)
    planned_batch_quantity: Optional[float] = Field(default=None, ge=0)
    actual_batch_quantity: Optional[float] = Field(default=None, ge=0)
    rejected_quantity: Optional[float] = Field(default=None, ge=0)
    scrap_quantity: Optional[float] = Field(default=None, ge=0)
    rework_quantity: Optional[float] = Field(default=None, ge=0)
    unit: Optional[str] = None
    yield_percent: Optional[float] = None
    variance_quantity: Optional[float] = None
    variance_percent: Optional[float] = None
    execution_status: Optional[YieldExecutionStatus] = None
    executed_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    status: Optional[YieldReconciliationStatus] = None
    disposition: Optional[YieldDisposition] = None
    calculated_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    deviation_id: Optional[str] = None
    comments: Optional[str] = None
    signature_ids: Optional[list[str]] = None
    evidence_document_ids: Optional[list[str]] = None
    planned_vs_actual_comparison: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "YieldReconciliationRecordUpdate":
        self.calculated_at = ensure_utc(self.calculated_at)
        self.executed_at = ensure_utc(self.executed_at)
        self.reviewed_at = ensure_utc(self.reviewed_at)
        self.approved_at = ensure_utc(self.approved_at)
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
