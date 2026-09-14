from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentWorkflowStatus(str, Enum):
    DRAFT = "Draft"
    IN_REVIEW = "In-Review"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    CANCELLED = "Cancelled"
    COMPLETED = "Completed"


class DocumentWorkflowStepStatus(str, Enum):
    PENDING = "Pending"
    IN_PROGRESS = "In-Progress"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    SKIPPED = "Skipped"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class DocumentWorkflowStep(BaseModel):
    step_id: str = Field(
        default_factory=lambda: f"document-workflow-step-{uuid4().hex[:12]}"
    )
    step_name: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=1)
    assignee_id: str | None = None
    assignee_name: str | None = None
    status: DocumentWorkflowStepStatus = DocumentWorkflowStepStatus.PENDING
    due_at: datetime | None = None
    completed_at: datetime | None = None
    comments: str | None = None

    @model_validator(mode="after")
    def normalize_dates(self):
        self.due_at = ensure_utc(self.due_at)
        self.completed_at = ensure_utc(self.completed_at)
        return self


class DocumentWorkflowBase(BaseModel):
    document_id: str = Field(..., min_length=1)
    workflow_name: str = Field(..., min_length=1)
    workflow_type: str | None = None
    status: DocumentWorkflowStatus = DocumentWorkflowStatus.DRAFT
    current_step_id: str | None = None
    initiated_by: str | None = None
    owner_id: str | None = None
    reviewer_ids: list[str] = Field(default_factory=list)
    approver_ids: list[str] = Field(default_factory=list)
    steps: list[DocumentWorkflowStep] = Field(default_factory=list)
    due_at: datetime | None = None
    completed_at: datetime | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_select_step(self):
        self.due_at = ensure_utc(self.due_at)
        self.completed_at = ensure_utc(self.completed_at)
        self.created_at = ensure_utc(self.created_at)
        if not self.current_step_id:
            active = [
                step
                for step in sorted(self.steps, key=lambda item: item.sequence)
                if step.status
                in {
                    DocumentWorkflowStepStatus.PENDING,
                    DocumentWorkflowStepStatus.IN_PROGRESS,
                }
            ]
            if active:
                self.current_step_id = active[0].step_id
        return self


class DocumentWorkflowCreate(DocumentWorkflowBase):
    """Schema for creating a document workflow."""


class DocumentWorkflowUpdate(BaseModel):
    document_id: str | None = None
    workflow_name: str | None = None
    workflow_type: str | None = None
    status: DocumentWorkflowStatus | None = None
    current_step_id: str | None = None
    initiated_by: str | None = None
    owner_id: str | None = None
    reviewer_ids: list[str] | None = None
    approver_ids: list[str] | None = None
    steps: list[DocumentWorkflowStep] | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class DocumentWorkflowStepUpdate(BaseModel):
    step_name: str | None = None
    sequence: int | None = Field(default=None, ge=1)
    assignee_id: str | None = None
    assignee_name: str | None = None
    status: DocumentWorkflowStepStatus | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    comments: str | None = None


class DocumentWorkflowResponse(DocumentWorkflowBase):
    workflow_id: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedDocumentWorkflowResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[DocumentWorkflowResponse]
