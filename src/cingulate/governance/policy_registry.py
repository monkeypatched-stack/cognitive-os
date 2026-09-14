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
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


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

    async def sync_from_pcp(self, db=None) -> int:
        """Pull role-permission mappings from the Policy Control Plane and register
        each role as a SECURITY policy.  Returns the number of policies synced.

        The PCP is the runtime distribution layer; the PolicyRegistry is the
        governance authority. Syncing bridges them so cingulate owns the canonical
        view of what policies exist.
        """
        try:
            from services.common.policy_control_plane import get_pcp

            pcp = await get_pcp(db=db)
            roles = await pcp.list_roles()
            synced = 0
            for role in roles:
                policy_id = f"ztaa-role-{role.get('role_id', 'unknown')}"
                if policy_id in self._policies:
                    continue
                p = Policy(
                    policy_id=policy_id,
                    name=role.get("name", role.get("role_id", "")),
                    category=PolicyCategory.SECURITY,
                    version="1.0.0",
                    description=f"ZTAA role-permission binding for role {role.get('role_id')}",
                    rules=[
                        {
                            "role_id": role.get("role_id"),
                            "permissions": role.get("permissions", []),
                            "parent_role_ids": role.get("parent_role_ids", []),
                        }
                    ],
                )
                self._policies[policy_id] = p
                synced += 1
            return synced
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("PCP sync failed (non-fatal): %s", exc)
            return 0


_registry: PolicyRegistry | None = None


def get_policy_registry() -> PolicyRegistry:
    """Return the process-level singleton PolicyRegistry."""
    global _registry
    if _registry is None:
        _registry = PolicyRegistry()
    return _registry
