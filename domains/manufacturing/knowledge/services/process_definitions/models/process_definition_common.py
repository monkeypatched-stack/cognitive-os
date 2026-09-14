"""
process_definition/enums.py
-----------------
Shared enumerations used across all process_definition models.
"""

from enum import Enum


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Status(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ConditionOperator(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    CONTAINS = "contains"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


class ConstraintType(str, Enum):
    RESOURCE = "resource"  # e.g. "must not access DB during migration"
    TEMPORAL = "temporal"  # e.g. "must not run between 09:00–17:00"
    ENVIRONMENTAL = "environmental"  # e.g. "must not run in production"
    OPERATIONAL = "operational"  # e.g. "must not restart service X"
    SECURITY = "security"  # e.g. "must not expose credentials"
    CONCURRENCY = "concurrency"  # e.g. "only one instance may run at a time"


class ActionType(str, Enum):
    MANUAL = "manual"  # Human must perform
    AUTOMATED = "automated"  # System can execute automatically
    HYBRID = "hybrid"  # Combination of both
