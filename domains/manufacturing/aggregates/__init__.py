"""Manufacturing aggregate roots.

Each aggregate root enforces domain invariants for its bounded context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class WorkOrderStatus(StrEnum):
    CREATED = "created"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    CANCELLED = "cancelled"


class BatchStatus(StrEnum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    RELEASED = "released"
    ON_HOLD = "on_hold"
    REJECTED = "rejected"


class SOPStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    ARCHIVED = "archived"


@dataclass
class WorkOrderAggregate:
    """Aggregate root for work orders."""

    work_order_id: str = ""
    title: str = ""
    description: str = ""
    priority: str = "medium"
    status: WorkOrderStatus = WorkOrderStatus.CREATED
    assigned_to: str = ""
    equipment_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    def assign(self, worker_id: str) -> None:
        if self.status != WorkOrderStatus.CREATED:
            raise ValueError(f"Cannot assign work order in {self.status} state")
        self.assigned_to = worker_id
        self.status = WorkOrderStatus.ASSIGNED

    def start(self) -> None:
        if self.status != WorkOrderStatus.ASSIGNED:
            raise ValueError("Work order must be assigned before starting")
        self.status = WorkOrderStatus.IN_PROGRESS

    def complete(self) -> None:
        if self.status != WorkOrderStatus.IN_PROGRESS:
            raise ValueError("Work order must be in progress to complete")
        self.status = WorkOrderStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)

    def hold(self) -> None:
        if self.status not in (WorkOrderStatus.ASSIGNED, WorkOrderStatus.IN_PROGRESS):
            raise ValueError(f"Cannot hold work order in {self.status} state")
        self.status = WorkOrderStatus.ON_HOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.work_order_id,
            "title": self.title,
            "status": self.status.value,
            "priority": self.priority,
            "assigned_to": self.assigned_to,
        }


@dataclass
class BatchAggregate:
    """Aggregate root for manufacturing batches."""

    batch_id: str = ""
    product: str = ""
    quantity: int = 0
    status: BatchStatus = BatchStatus.CREATED
    operations: list[str] = field(default_factory=list)
    completed_operations: list[str] = field(default_factory=list)
    quality_metrics: dict[str, float] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def start(self) -> None:
        if self.status != BatchStatus.CREATED:
            raise ValueError(f"Cannot start batch in {self.status} state")
        self.status = BatchStatus.IN_PROGRESS

    def complete_operation(self, operation: str) -> None:
        if operation not in self.operations:
            raise ValueError(f"Unknown operation: {operation}")
        self.completed_operations.append(operation)

    def submit_for_review(self) -> None:
        if self.status != BatchStatus.IN_PROGRESS:
            raise ValueError("Batch must be in progress to submit for review")
        self.status = BatchStatus.REVIEW

    def release(self) -> None:
        if self.status != BatchStatus.REVIEW:
            raise ValueError("Batch must be reviewed before release")
        self.status = BatchStatus.RELEASED

    def hold(self, reason: str = "") -> None:
        self.status = BatchStatus.ON_HOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.batch_id,
            "product": self.product,
            "quantity": self.quantity,
            "status": self.status.value,
            "quality_metrics": self.quality_metrics,
        }


@dataclass
class SOPAggregate:
    """Aggregate root for standard operating procedures."""

    sop_id: str = ""
    title: str = ""
    version: str = "1.0"
    status: SOPStatus = SOPStatus.DRAFT
    steps: list[str] = field(default_factory=list)
    approved_by: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def submit_for_approval(self) -> None:
        if self.status != SOPStatus.DRAFT:
            raise ValueError("Only draft SOPs can be submitted for approval")
        self.status = SOPStatus.PENDING_APPROVAL

    def approve(self, approver: str) -> None:
        if self.status != SOPStatus.PENDING_APPROVAL:
            raise ValueError("SOP must be pending approval")
        self.status = SOPStatus.APPROVED
        self.approved_by = approver

    def archive(self) -> None:
        self.status = SOPStatus.ARCHIVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.sop_id,
            "title": self.title,
            "version": self.version,
            "status": self.status.value,
            "steps": len(self.steps),
        }


@dataclass
class ChangeControlAggregate:
    """Aggregate root for change control records."""

    change_id: str = ""
    title: str = ""
    description: str = ""
    risk_level: str = "low"
    status: str = "submitted"
    approvals: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def approve(self, approver: str) -> None:
        self.approvals.append(approver)

    def implement(self) -> None:
        if len(self.approvals) < 1:
            raise ValueError("Change control needs at least 1 approval")
        self.status = "implemented"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.change_id,
            "title": self.title,
            "status": self.status,
            "risk_level": self.risk_level,
            "approvals": len(self.approvals),
        }


@dataclass
class CalibrationRecordAggregate:
    """Aggregate root for calibration records."""

    record_id: str = ""
    equipment_id: str = ""
    calibration_type: str = ""
    result: str = "pending"
    next_due: datetime | None = None
    performed_at: datetime | None = None

    def record_result(self, result: str) -> None:
        self.result = result
        self.performed_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.record_id,
            "equipment_id": self.equipment_id,
            "result": self.result,
            "calibration_type": self.calibration_type,
        }


@dataclass
class MaintenanceEventAggregate:
    """Aggregate root for maintenance events."""

    event_id: str = ""
    equipment_id: str = ""
    event_type: str = ""
    status: str = "scheduled"
    actions_taken: list[str] = field(default_factory=list)
    scheduled_at: datetime | None = None
    completed_at: datetime | None = None

    def start(self) -> None:
        self.status = "in_progress"

    def complete(self) -> None:
        self.status = "completed"
        self.completed_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "equipment_id": self.equipment_id,
            "status": self.status,
            "event_type": self.event_type,
        }
