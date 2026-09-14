from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

IpcResultStatus = Literal[
    "Pending", "Pass", "Fail", "Retest Required", "Not Applicable"
]
IpcReviewStatus = Literal["Pending", "Reviewed", "Approved", "Rejected"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class IpcLimitRecord(BaseModel):
    parameter: str = Field(..., min_length=1)
    target: Optional[float | str] = None
    lower_limit: Optional[float] = None
    upper_limit: Optional[float] = None
    unit: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    cqa_ids: list[str] = Field(default_factory=list)
    cpp_ids: list[str] = Field(default_factory=list)


class IpcMeasuredResult(BaseModel):
    parameter: str = Field(..., min_length=1)
    measured_value: Optional[float] = None
    text_value: Optional[str] = None
    unit: Optional[str] = None
    result: IpcResultStatus = "Pending"

    @model_validator(mode="after")
    def validate_value(self) -> "IpcMeasuredResult":
        if (
            self.result not in {"Pending", "Not Applicable"}
            and self.measured_value is None
            and not self.text_value
        ):
            raise ValueError(
                "non-pending IPC measured results must include measured_value or text_value."
            )
        return self


class IpcResultRecord(BaseModel):
    ipc_result_id: str = Field(..., min_length=1)
    ipc_checkpoint_id: str = Field(..., min_length=1)
    batch_execution_record_id: str = Field(..., min_length=1)
    batch_step_execution_id: Optional[str] = None
    batch_id: str = Field(..., min_length=1)

    process_definition_id: str = Field(..., min_length=1)
    process_step_id: str = Field(..., min_length=1)
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    product_id: Optional[str] = None
    bom_id: Optional[str] = None

    method: str = Field(..., min_length=1)
    method_reference: str = Field(..., min_length=1)
    sampling_method: Optional[str] = None
    sample_id: Optional[str] = None
    sample_location: Optional[str] = None
    sampled_by: Optional[str] = None
    sampled_at: Optional[datetime] = None
    tested_by: str = Field(..., min_length=1)
    tested_at: datetime

    limits_snapshot: list[IpcLimitRecord] = Field(default_factory=list)
    measured_results: list[IpcMeasuredResult] = Field(default_factory=list)
    status: IpcResultStatus = "Pending"
    review_status: IpcReviewStatus = "Pending"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    deviation_id: Optional[str] = None
    evidence_document_ids: list[str] = Field(default_factory=list)
    signature_ids: list[str] = Field(default_factory=list)
    audit_event_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "IpcResultRecord":
        self.sampled_at = ensure_utc(self.sampled_at)
        self.tested_at = ensure_utc(self.tested_at) or utc_now()
        self.reviewed_at = ensure_utc(self.reviewed_at)
        self.approved_at = ensure_utc(self.approved_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        if self.sampled_at and self.tested_at and self.tested_at < self.sampled_at:
            raise ValueError(
                "tested_at cannot be before sampled_at for IPC result records."
            )
        if self.status in {"Pass", "Fail", "Retest Required"}:
            if not self.limits_snapshot:
                raise ValueError(
                    "completed IPC result records must include limits_snapshot."
                )
            if not self.measured_results:
                raise ValueError(
                    "completed IPC result records must include measured_results."
                )
            if not self.evidence_document_ids:
                raise ValueError(
                    "completed IPC result records must include evidence_document_ids."
                )
            if not self.signature_ids:
                raise ValueError(
                    "completed IPC result records must include signature_ids."
                )
        if self.status == "Fail" and not self.deviation_id:
            raise ValueError("failed IPC result records must include deviation_id.")
        if self.review_status in {"Reviewed", "Approved"} and (
            not self.reviewed_by or not self.reviewed_at
        ):
            raise ValueError(
                "reviewed/approved IPC result records must include reviewed_by and reviewed_at."
            )
        if self.review_status == "Approved" and (
            not self.approved_by or not self.approved_at
        ):
            raise ValueError(
                "approved IPC result records must include approved_by and approved_at."
            )
        return self


class IpcResultRecordCreate(IpcResultRecord):
    pass


class IpcResultRecordUpdate(BaseModel):
    sample_id: Optional[str] = None
    sample_location: Optional[str] = None
    sampled_by: Optional[str] = None
    sampled_at: Optional[datetime] = None
    tested_by: Optional[str] = None
    tested_at: Optional[datetime] = None
    limits_snapshot: Optional[list[IpcLimitRecord]] = None
    measured_results: Optional[list[IpcMeasuredResult]] = None
    status: Optional[IpcResultStatus] = None
    review_status: Optional[IpcReviewStatus] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    deviation_id: Optional[str] = None
    evidence_document_ids: Optional[list[str]] = None
    signature_ids: Optional[list[str]] = None
    audit_event_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "IpcResultRecordUpdate":
        self.sampled_at = ensure_utc(self.sampled_at)
        self.tested_at = ensure_utc(self.tested_at)
        self.reviewed_at = ensure_utc(self.reviewed_at)
        self.approved_at = ensure_utc(self.approved_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class IpcResultRecordResponse(IpcResultRecord):
    class Config:
        from_attributes = True


class PaginatedIpcResultRecordResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[IpcResultRecordResponse]
