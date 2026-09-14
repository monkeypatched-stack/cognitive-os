from datetime import datetime, timezone
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, model_validator

# ─────────────────────────────────────────────
# LITERALS
# ─────────────────────────────────────────────

WorkOrderStatus = Literal[
    "Todo",
    "In Progress",
    "Ready for Next Stage",
    "Rejected",
    "Completed",
    "Done",
    "Cancelled",
    "On Hold",
    "Scheduled",
]

WorkOrderPriority = Literal[
    "Low",
    "Medium",
    "High",
    "Critical",
]

WorkOrderType = Literal[
    "Maintenance",
    "Inspection",
    "Calibration",
    "Cleaning",
    "Repair",
    "Investigation",
    "Other",
]

# ─────────────────────────────────────────────
# UTILS (INLINE SAFE UTC HANDLING)
# ─────────────────────────────────────────────


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ─────────────────────────────────────────────
# BASE MODEL
# ─────────────────────────────────────────────


class WorkOrder(BaseModel):
    work_order_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    message: Optional[str] = None

    work_order_type: WorkOrderType = "Other"
    status: WorkOrderStatus = "Todo"
    priority: WorkOrderPriority = "Medium"

    # Linkage
    parent_work_order_id: Optional[str] = None
    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None
    line_id: Optional[str] = None
    circuit_id: Optional[str] = None
    workstation_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    product_id: Optional[str] = None
    component_id: Optional[str] = None
    machine_part_id: Optional[str] = None
    raw_material_id: Optional[str] = None

    # Assignment
    assigned_to: Optional[str] = None
    assigned_user_id: Optional[str] = None
    assigned_user_name: Optional[str] = None
    assigned_team_id: Optional[str] = None
    assigned_team_name: Optional[str] = None

    # Dates
    created_at: datetime = Field(default_factory=utc_now)
    expected_completion: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_time_hrs: Optional[int] = None
    updated_at: datetime = Field(default_factory=utc_now)

    # Computed
    is_overdue: bool = False

    remarks: Optional[str] = None
    attachments: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_and_validate(self):
        # 1. Normalize all datetimes to UTC-aware
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        self.expected_completion = ensure_utc(self.expected_completion)
        self.completed_at = ensure_utc(self.completed_at)

        # 2. Validate business rule: completed_at only if terminal completed
        if self.completed_at and self.status not in ("Done", "Completed"):
            raise ValueError(
                "'completed_at' can only be set when status is 'Done' or 'Completed'."
            )

        # 3. Validate chronological order
        if self.completed_at and self.completed_at < self.created_at:
            raise ValueError("'completed_at' cannot be before 'created_at'.")

        # 4. Compute overdue safely
        now = utc_now()

        if (
            self.status not in ("Done", "Completed", "Cancelled")
            and self.expected_completion
        ):
            self.is_overdue = now > self.expected_completion
        else:
            self.is_overdue = False

        return self


# ─────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────


class WorkOrderCreate(WorkOrder):
    pass


class WorkOrderSubtaskCreate(BaseModel):
    work_order_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    message: Optional[str] = None
    work_order_type: WorkOrderType = "Other"
    status: WorkOrderStatus = "Todo"
    priority: WorkOrderPriority = "Medium"
    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None
    line_id: Optional[str] = None
    circuit_id: Optional[str] = None
    workstation_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    product_id: Optional[str] = None
    component_id: Optional[str] = None
    machine_part_id: Optional[str] = None
    raw_material_id: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_user_id: Optional[str] = None
    assigned_user_name: Optional[str] = None
    assigned_team_id: Optional[str] = None
    assigned_team_name: Optional[str] = None
    created_by: Optional[str] = None
    expected_completion: Optional[datetime] = None
    remarks: Optional[str] = None
    attachments: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "WorkOrderSubtaskCreate":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        self.expected_completion = ensure_utc(self.expected_completion)
        return self


class WorkOrderSubtaskUpdate(BaseModel):
    title: Optional[str] = None
    message: Optional[str] = None
    work_order_type: Optional[WorkOrderType] = None
    status: Optional[WorkOrderStatus] = None
    priority: Optional[WorkOrderPriority] = None
    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_team_id: Optional[str] = None
    assigned_team_name: Optional[str] = None
    expected_completion: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    remarks: Optional[str] = None
    attachments: Optional[List[str]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "WorkOrderSubtaskUpdate":
        self.expected_completion = ensure_utc(self.expected_completion)
        self.completed_at = ensure_utc(self.completed_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


# ─────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────


class WorkOrderUpdate(BaseModel):
    title: Optional[str] = None
    message: Optional[str] = None
    work_order_type: Optional[WorkOrderType] = None
    status: Optional[WorkOrderStatus] = None
    priority: Optional[WorkOrderPriority] = None

    parent_work_order_id: Optional[str] = None
    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None
    line_id: Optional[str] = None
    circuit_id: Optional[str] = None
    workstation_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    product_id: Optional[str] = None
    component_id: Optional[str] = None
    machine_part_id: Optional[str] = None
    raw_material_id: Optional[str] = None

    assigned_to: Optional[str] = None
    assigned_user_id: Optional[str] = None
    assigned_user_name: Optional[str] = None
    assigned_team_id: Optional[str] = None
    assigned_team_name: Optional[str] = None

    expected_completion: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    remarks: Optional[str] = None
    attachments: Optional[List[str]] = None

    updated_at: datetime = Field(default_factory=utc_now)


# ─────────────────────────────────────────────
# RESPONSE & PAGINATION
# ─────────────────────────────────────────────


class WorkOrderResponse(WorkOrder):
    class Config:
        from_attributes = True


class PaginatedWorkOrderResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[WorkOrderResponse]


class WorkOrderCountStatsResponse(BaseModel):
    total: int
    open: int
    completed: int
    overdue: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
