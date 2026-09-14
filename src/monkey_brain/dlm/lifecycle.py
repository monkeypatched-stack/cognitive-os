"""Lifecycle Policy — defines data lifecycle rules.

Every persisted object must define:
- Creation Policy
- Retention Policy
- Expiration Policy
- Archival Policy
- Deletion Policy
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StorageClass(str, Enum):
    """Data storage tiers."""

    PERMANENT = "permanent"  # Never automatically deleted
    LONG_TERM = "long_term"  # Retained for configurable periods
    OPERATIONAL = "operational"  # Retained for operational analysis
    EPHEMERAL = "ephemeral"  # Automatically removed


class ExpirationAction(str, Enum):
    """Actions when data expires."""

    DELETE = "delete"
    ARCHIVE = "archive"
    COMPRESS = "compress"
    MOVE_TO_COLD = "move_to_cold"


@dataclass
class LifecyclePolicy:
    """Defines the lifecycle of a data type."""

    name: str = ""
    storage_class: StorageClass = StorageClass.OPERATIONAL

    # TTL (seconds) - None means no expiration
    ttl_seconds: int | None = None

    # Retention
    max_age_seconds: int | None = None
    max_count: int | None = None

    # Expiration
    expiration_action: ExpirationAction = ExpirationAction.DELETE

    # Archival
    archive_after_seconds: int | None = None
    archive_to: str | None = None

    # Compression
    compress_after_seconds: int | None = None

    # Safety
    protected: bool = False  # Never delete if True

    # Metadata
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, age_seconds: float) -> bool:
        """Check if data has expired."""
        if self.ttl_seconds is None:
            return False
        return age_seconds >= self.ttl_seconds

    def should_archive(self, age_seconds: float) -> bool:
        """Check if data should be archived."""
        if self.archive_after_seconds is None:
            return False
        return age_seconds >= self.archive_after_seconds

    def should_compress(self, age_seconds: float) -> bool:
        """Check if data should be compressed."""
        if self.compress_after_seconds is None:
            return False
        return age_seconds >= self.compress_after_seconds


# --- Default Policies ---

PERMANENT_POLICY = LifecyclePolicy(
    name="permanent",
    storage_class=StorageClass.PERMANENT,
    protected=True,
    description="Never automatically deleted",
)

LONG_TERM_POLICY = LifecyclePolicy(
    name="long_term",
    storage_class=StorageClass.LONG_TERM,
    ttl_seconds=86400 * 365,  # 1 year
    archive_after_seconds=86400 * 180,  # 6 months
    description="Retained for 1 year, archived after 6 months",
)

OPERATIONAL_POLICY = LifecyclePolicy(
    name="operational",
    storage_class=StorageClass.OPERATIONAL,
    ttl_seconds=86400 * 90,  # 90 days
    archive_after_seconds=86400 * 30,  # 30 days
    description="Retained for 90 days",
)

EPHEMERAL_POLICY = LifecyclePolicy(
    name="ephemeral",
    storage_class=StorageClass.EPHEMERAL,
    ttl_seconds=3600,  # 1 hour
    expiration_action=ExpirationAction.DELETE,
    description="Automatically removed after 1 hour",
)


# --- Data Type Policies ---

DATA_POLICIES: dict[str, LifecyclePolicy] = {
    # Permanent
    "capability": PERMANENT_POLICY,
    "workflow": PERMANENT_POLICY,
    "policy": PERMANENT_POLICY,
    "configuration": PERMANENT_POLICY,
    "ontology": PERMANENT_POLICY,
    # Long-term
    "audit": LONG_TERM_POLICY,
    "memory": LONG_TERM_POLICY,
    "batch_history": LONG_TERM_POLICY,
    # Operational
    "metric": OPERATIONAL_POLICY,
    "log": OPERATIONAL_POLICY,
    "trace": OPERATIONAL_POLICY,
    "execution": OPERATIONAL_POLICY,
    # Ephemeral
    "session": EPHEMERAL_POLICY,
    "cache": EPHEMERAL_POLICY,
    "temporary": EPHEMERAL_POLICY,
    "snapshot": EPHEMERAL_POLICY,
}


def get_policy(data_type: str) -> LifecyclePolicy:
    """Get lifecycle policy for a data type."""
    return DATA_POLICIES.get(data_type, OPERATIONAL_POLICY)
