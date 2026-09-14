from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

BatchReleaseStatus = Literal[
    "Draft", "In Review", "Ready For QA Release", "Released", "Rejected", "Blocked"
]
BatchReleaseDecision = Literal["Pending", "Released", "Rejected", "Hold"]
PrerequisiteStatus = Literal["Pending", "Passed", "Failed", "Waived"]
ExecutedPackageStatus = Literal[
    "Pending", "Executed", "Reviewed", "Approved", "Rejected"
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class BatchReleasePrerequisite(BaseModel):
    name: str = Field(..., min_length=1)
    status: PrerequisiteStatus = "Pending"
    owner_role: Optional[str] = None
    owner_id: Optional[str] = None
    evidence_document_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class ExecutedBatchRecordPackage(BaseModel):
    package_type: Literal["BMR", "BPR"]
    status: ExecutedPackageStatus = "Pending"
    document_ids: list[str] = Field(default_factory=list)
    executed_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    evidence_document_ids: list[str] = Field(default_factory=list)
    report_template_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "ExecutedBatchRecordPackage":
        self.executed_at = ensure_utc(self.executed_at)
        self.reviewed_at = ensure_utc(self.reviewed_at)
        if self.status in {"Executed", "Reviewed", "Approved"}:
            if not self.document_ids:
                raise ValueError(
                    f"executed {self.package_type} package must include document_ids."
                )
            if not self.executed_by or not self.executed_at:
                raise ValueError(
                    f"executed {self.package_type} package must include executed_by and executed_at."
                )
        if self.status in {"Reviewed", "Approved"} and (
            not self.reviewed_by or not self.reviewed_at
        ):
            raise ValueError(
                f"reviewed/approved {self.package_type} package must include reviewed_by and reviewed_at."
            )
        return self


class BatchReleaseWorkflow(BaseModel):
    batch_release_workflow_id: str = Field(..., min_length=1)
    batch_execution_record_id: str = Field(..., min_length=1)
    batch_id: str = Field(..., min_length=1)
    product_id: Optional[str] = None
    bom_id: Optional[str] = None
    process_definition_id: Optional[str] = None
    process_route_id: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None

    status: BatchReleaseStatus = "Draft"
    release_decision: BatchReleaseDecision = "Pending"
    release_scope: str = Field(default="Executed BMR/BPR batch release", min_length=1)

    bmr_document_ids: list[str] = Field(default_factory=list)
    bpr_document_ids: list[str] = Field(default_factory=list)
    executed_bmr_package: Optional[ExecutedBatchRecordPackage] = None
    executed_bpr_package: Optional[ExecutedBatchRecordPackage] = None
    batch_step_execution_ids: list[str] = Field(default_factory=list)
    cleaning_record_ids: list[str] = Field(default_factory=list)
    equipment_usage_ids: list[str] = Field(default_factory=list)
    area_room_usage_ids: list[str] = Field(default_factory=list)
    yield_reconciliation_record_ids: list[str] = Field(default_factory=list)
    cpp_cqa_registry_ids: list[str] = Field(default_factory=list)
    evidence_document_ids: list[str] = Field(default_factory=list)
    report_template_ids: list[str] = Field(default_factory=list)

    prerequisites: list[BatchReleasePrerequisite] = Field(default_factory=list)
    qa_reviewer_id: Optional[str] = None
    released_by: Optional[str] = None
    released_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    signature_ids: list[str] = Field(default_factory=list)
    audit_event_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "BatchReleaseWorkflow":
        self.released_at = ensure_utc(self.released_at)
        self.rejected_at = ensure_utc(self.rejected_at)
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()

        if self.status == "Released" or self.release_decision == "Released":
            if not self.qa_reviewer_id:
                raise ValueError(
                    "released batch release workflows must include qa_reviewer_id."
                )
            if not self.released_by or not self.released_at:
                raise ValueError(
                    "released batch release workflows must include released_by and released_at."
                )
            if not self.signature_ids:
                raise ValueError(
                    "released batch release workflows must include signature_ids."
                )
            if not self.bmr_document_ids or not self.bpr_document_ids:
                raise ValueError(
                    "released batch release workflows must include executed BMR and BPR document references."
                )
            if (
                not self.executed_bmr_package
                or self.executed_bmr_package.status
                not in {"Executed", "Reviewed", "Approved"}
            ):
                raise ValueError(
                    "released batch release workflows must include an executed BMR package."
                )
            if (
                not self.executed_bpr_package
                or self.executed_bpr_package.status
                not in {"Executed", "Reviewed", "Approved"}
            ):
                raise ValueError(
                    "released batch release workflows must include an executed BPR package."
                )
            if not self.report_template_ids:
                raise ValueError(
                    "released batch release workflows must include report_template_ids."
                )
            if not self.executed_bmr_package.report_template_ids:
                raise ValueError(
                    "released batch release workflows must bind the executed BMR package to report_template_ids."
                )
            if not self.executed_bpr_package.report_template_ids:
                raise ValueError(
                    "released batch release workflows must bind the executed BPR package to report_template_ids."
                )
            if not self.batch_step_execution_ids:
                raise ValueError(
                    "released batch release workflows must include batch_step_execution_ids."
                )
            if not self.yield_reconciliation_record_ids:
                raise ValueError(
                    "released batch release workflows must include yield_reconciliation_record_ids."
                )
            failed = [
                item.name
                for item in self.prerequisites
                if item.status not in {"Passed", "Waived"}
            ]
            if failed:
                raise ValueError(
                    f"released batch release workflows have unresolved prerequisites: {', '.join(failed)}."
                )
        if self.status == "Rejected" or self.release_decision == "Rejected":
            if not self.rejection_reason:
                raise ValueError(
                    "rejected batch release workflows must include rejection_reason."
                )
        return self


class BatchReleaseWorkflowCreate(BatchReleaseWorkflow):
    pass


class BatchReleaseWorkflowUpdate(BaseModel):
    status: Optional[BatchReleaseStatus] = None
    release_decision: Optional[BatchReleaseDecision] = None
    release_scope: Optional[str] = None
    bmr_document_ids: Optional[list[str]] = None
    bpr_document_ids: Optional[list[str]] = None
    executed_bmr_package: Optional[ExecutedBatchRecordPackage] = None
    executed_bpr_package: Optional[ExecutedBatchRecordPackage] = None
    report_template_ids: Optional[list[str]] = None
    batch_step_execution_ids: Optional[list[str]] = None
    cleaning_record_ids: Optional[list[str]] = None
    equipment_usage_ids: Optional[list[str]] = None
    area_room_usage_ids: Optional[list[str]] = None
    yield_reconciliation_record_ids: Optional[list[str]] = None
    cpp_cqa_registry_ids: Optional[list[str]] = None
    evidence_document_ids: Optional[list[str]] = None
    prerequisites: Optional[list[BatchReleasePrerequisite]] = None
    qa_reviewer_id: Optional[str] = None
    released_by: Optional[str] = None
    released_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    signature_ids: Optional[list[str]] = None
    audit_event_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "BatchReleaseWorkflowUpdate":
        self.released_at = ensure_utc(self.released_at)
        self.rejected_at = ensure_utc(self.rejected_at)
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        return self


class BatchReleaseWorkflowResponse(BatchReleaseWorkflow):
    class Config:
        from_attributes = True


class PaginatedBatchReleaseWorkflowResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[BatchReleaseWorkflowResponse]
