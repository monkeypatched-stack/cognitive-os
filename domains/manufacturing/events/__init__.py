"""Manufacturing Domain Events.

DDD Domain Events — things that happened in the manufacturing domain.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class WorkOrderCreated:
    """Event: a work order was created."""

    work_order_id: str
    title: str
    assigned_to: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class WorkOrderCompleted:
    """Event: a work order was completed."""

    work_order_id: str
    completed_by: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class BatchReleased:
    """Event: a batch was released for distribution."""

    batch_id: str
    batch_number: str
    released_by: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class BatchOnHold:
    """Event: a batch was put on hold."""

    batch_id: str
    batch_number: str
    reason: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class SOPApproved:
    """Event: an SOP was approved."""

    sop_id: str
    approved_by: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class ChangeControlSubmitted:
    """Event: a change control was submitted."""

    change_control_id: str
    title: str
    submitted_by: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class CalibrationPerformed:
    """Event: calibration was performed on equipment."""

    equipment_id: str
    equipment_name: str
    result: str  # Pass/Fail
    performed_by: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class MaintenanceCompleted:
    """Event: maintenance was completed."""

    equipment_id: str
    equipment_name: str
    event_type: str  # preventive/corrective/breakdown
    technician: str = ""
    downtime_minutes: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
