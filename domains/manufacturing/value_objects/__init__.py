"""Manufacturing Domain Value Objects.

DDD Value Objects — immutable objects defined by their attributes.
"""

from dataclasses import dataclass
from enum import Enum


class WorkOrderStatus(Enum):
    """Work order status value object."""

    TODO = "Todo"
    IN_PROGRESS = "In Progress"
    ON_HOLD = "On Hold"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class BatchStatus(Enum):
    """Batch status value object."""

    CREATED = "Created"
    IN_PROGRESS = "In Progress"
    ON_HOLD = "On Hold"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    RELEASED = "Released"


class Priority(Enum):
    """Priority value object."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class CalibrationResult(Enum):
    """Calibration result value object."""

    PASS = "Pass"
    FAIL = "Fail"
    IN_PROGRESS = "In Progress"
    DUE = "Due"
    OVERDUE = "Overdue"


@dataclass(frozen=True)
class CycleTime:
    """Cycle time value object."""

    target_seconds: float
    actual_seconds: float

    @property
    def efficiency(self) -> float:
        if self.target_seconds == 0:
            return 0.0
        return self.actual_seconds / self.target_seconds

    @property
    def is_bottleneck(self) -> bool:
        return self.efficiency > 1.0


@dataclass(frozen=True)
class Location:
    """Location value object — hierarchical plant location."""

    plant_id: str
    line_id: str = ""
    stage_id: str = ""
    workstation_id: str = ""
