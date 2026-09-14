"""Manufacturing Domain Entities.

DDD Entities — objects with identity that represent domain concepts.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class WorkOrder:
    """Work order entity — represents a unit of work in the manufacturing process."""

    id: str
    title: str
    status: str
    assigned_to: str = ""
    priority: str = "normal"
    type: str = ""  # calibration, maintenance, installation, etc.
    equipment_id: str = ""
    due_date: str = ""


@dataclass
class Batch:
    """Batch entity — represents a production batch."""

    id: str
    batch_number: str
    product_name: str
    status: str
    yield_pct: float = 0.0
    quality_metrics: dict = None

    def __post_init__(self):
        if self.quality_metrics is None:
            self.quality_metrics = {}


@dataclass
class SOP:
    """SOP entity — Standard Operating Procedure."""

    id: str
    title: str
    version: str = ""
    department: str = ""
    line_id: str = ""
    stage_id: str = ""
    workstation_id: str = ""


@dataclass
class ChangeControl:
    """Change control entity — manages changes to validated systems."""

    id: str
    title: str
    status: str
    department: str = ""
    description: str = ""


@dataclass
class CalibrationRecord:
    """Calibration record entity — tracks equipment calibration."""

    id: str
    equipment_id: str
    equipment_name: str
    status: str
    last_calibrated: str = ""
    next_due: str = ""


@dataclass
class MaintenanceEvent:
    """Maintenance event entity — tracks maintenance activities."""

    id: str
    equipment_id: str
    equipment_name: str
    event_type: str  # preventive, corrective, breakdown
    status: str
    technician: str = ""
    parts_replaced: list = None
    downtime_minutes: float = 0.0

    def __post_init__(self):
        if self.parts_replaced is None:
            self.parts_replaced = []


@dataclass
class Equipment:
    """Equipment entity — physical equipment in the plant."""

    id: str
    name: str
    category: str
    manufacturer: str = ""
    status: str = "active"
    workstation_id: str = ""
    line_id: str = ""
    stage_id: str = ""


@dataclass
class Machine:
    """Machine entity — machines within workstations."""

    id: str
    name: str
    type_category: str
    manufacturer: str = ""
    serial_no: str = ""
    model_no: str = ""
    status: str = "Operational"
    workstation_id: str = ""
    line_id: str = ""
    stage_id: str = ""


@dataclass
class Worker:
    """Worker entity — personnel in the plant."""

    id: str
    name: str
    title: str
    role: str
    status: str = "Active"
    workstation_id: str = ""
    reports_to: str = ""
