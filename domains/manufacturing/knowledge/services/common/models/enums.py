from enum import Enum


class ShiftType(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    NIGHT = "night"
    SPLIT = "split"
    OVERTIME = "overtime"


class DayOfWeek(str, Enum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ChangeoverType(str, Enum):
    """What kind of changeover is this?"""

    PRODUCT_SWITCH = "product_switch"  # different SKU / recipe
    TOOLING_CHANGE = "tooling_change"  # swap jigs, dies, moulds
    CLEANING = "cleaning"  # sanitation / purge cycle
    CALIBRATION = "calibration"  # measurement / alignment reset
    MATERIAL_CHANGE = "material_change"  # different raw material / grade
    FORMAT_CHANGE = "format_change"  # packaging line size change
    FULL_TURNAROUND = "full_turnaround"  # combination of all above (SMED target)


class ChangeoverStatus(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABORTED = "aborted"
    OVERDUE = "overdue"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class TaskCategory(str, Enum):
    """SMED-style internal/external task classification."""

    INTERNAL = "internal"  # must be done while machine is stopped
    EXTERNAL = "external"  # can be done while machine is still running
    PARALLEL = "parallel"  # done concurrently with another task


class ChangeoverTrigger(str, Enum):
    SCHEDULED = "scheduled"  # planned in advance
    UNPLANNED = "unplanned"  # reactive / emergency
    QUALITY_REJECT = "quality_reject"  # triggered by QC failure
    SHIFT_HANDOVER = "shift_handover"  # between shifts
    CUSTOMER_ORDER = "customer_order"  # pull from order management
