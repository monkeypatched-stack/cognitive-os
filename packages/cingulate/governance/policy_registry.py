"""Policy Registry — centralized policy definitions.

All policies are versioned and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class PolicyCategory(str, Enum):
    SECURITY = "security"
    RUNTIME = "runtime"
    SCHEDULING = "scheduling"
    LIFECYCLE = "lifecycle"
    MEMORY = "memory"
    CAPABILITY = "capability"
    LEARNING = "learning"
    COST = "cost"
    DATA = "data"
    COMPLIANCE = "compliance"


@dataclass
class Policy:
    """A governance policy."""

    policy_id: str = field(default_factory=lambda: f"policy-{uuid4().hex[:8]}")
    name: str = ""
    category: PolicyCategory = PolicyCategory.RUNTIME
    version: str = "1.0.0"
    description: str = ""
    rules: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class PolicyRegistry:
    """Centralized policy definitions.

    All policies are versioned and auditable.
    """

    def __init__(self):
        self._policies: dict[str, Policy] = {}

    def register(self, policy: Policy) -> str:
        """Register a policy."""
        self._policies[policy.policy_id] = policy
        return policy.policy_id

    def get(self, policy_id: str) -> Policy | None:
        return self._policies.get(policy_id)

    def get_by_category(self, category: PolicyCategory) -> list[Policy]:
        return [p for p in self._policies.values() if p.category == category]

    def get_all(self) -> list[Policy]:
        return list(self._policies.values())

    def update(self, policy_id: str, **kwargs: Any) -> bool:
        if policy_id in self._policies:
            policy = self._policies[policy_id]
            for key, value in kwargs.items():
                if hasattr(policy, key):
                    setattr(policy, key, value)
            policy.updated_at = datetime.now(timezone.utc).isoformat()
            return True
        return False

    def remove(self, policy_id: str) -> bool:
        if policy_id in self._policies:
            del self._policies[policy_id]
            return True
        return False

    def summary(self) -> dict:
        categories = {}
        for p in self._policies.values():
            cat = p.category.value
            categories[cat] = categories.get(cat, 0) + 1
        return {
            "total": len(self._policies),
            "by_category": categories,
        }
