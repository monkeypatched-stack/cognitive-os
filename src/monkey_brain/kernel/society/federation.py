"""Federation (Step 12.9) — a federation of societies.

Rule 4 of the Step 12 architecture correction: "the federation itself is a
domain object. The runtime manages it." `Federation`/`FederationPolicy`
below are immutable data — construction-only, no behavior beyond simple
queries, same shape as `domain.py::Society`. `FederationManager` is the
mutating counterpart Planetary Runtime holds to satisfy its federation
MANAGEMENT responsibility (Runtime Responsibility Matrix), mirroring how
`SocietyRuntime` manages the immutable `Society` via `dataclasses.replace()`.

Closest prior art: `kernel/compile/tenancy.py`'s `TenantWorld` (private-per-
tenant + one shared tensor) — a different layer (the pipeline's tensor-based
world) solving a structurally similar problem: bounded units that share
SOME state while keeping the rest private. This module is the society-level
equivalent — societies (not tenants) as the federated unit, `Society`/
`SharedWorld` (not `SparseTransitionTensor`) as the underlying model. The
two are not merged: they operate on different data models at different
layers of the hierarchy.

Scope note (updated): `PlanetaryRuntime` now holds a registry of
`SocietyRuntime` instances (`add_society()`/`create_society()`), not just
its own primary society, and `federated_cycle(federation_id)` ticks every
member society this runtime actually has registered — real coordination,
not just data referencing. Member societies NOT in the local registry
(e.g. living on another node/process) are skipped and reported, not
errored — a federation can reference societies it doesn't locally run.

Still explicitly out of scope: world-STATE synchronization across member
societies. Each society's `SharedWorld` stays genuinely separate;
`federated_cycle()` only syncs coordination history (via
`FederationManager.record_cross_society_event()`), not entity/relationship
state. `SharedWorld` has no merge/diff primitive to build real world
synchronization on top of — inventing one is a separate, larger feature.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class FederationPolicyScope(Enum):
    ALL_MEMBERS = "all_members"
    SPECIFIC_SOCIETIES = "specific_societies"


@dataclass(frozen=True)
class FederationPolicy:
    """A policy governing how member societies relate to one another within
    a federation — distinct from `governance.py::GovernancePolicy`, which
    governs individual actors WITHIN one society. This governs societies
    relative to each other: what one society may share with another, how
    inter-society disputes are resolved, etc."""

    policy_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    description: str = ""
    scope: FederationPolicyScope = FederationPolicyScope.ALL_MEMBERS
    applies_to_society_ids: tuple[str, ...] = ()
    """Only consulted when scope is SPECIFIC_SOCIETIES."""
    rules: tuple[str, ...] = ()
    enabled: bool = True
    created_at: float = field(default_factory=time.time)

    def applies_to(self, society_id: str) -> bool:
        if not self.enabled:
            return False
        if self.scope == FederationPolicyScope.ALL_MEMBERS:
            return True
        return society_id in self.applies_to_society_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "description": self.description,
            "scope": self.scope.value,
            "applies_to_society_ids": list(self.applies_to_society_ids),
            "rules": list(self.rules),
            "enabled": self.enabled,
            "created_at": round(self.created_at, 6),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FederationPolicy:
        return cls(
            policy_id=d.get("policy_id", uuid4().hex),
            name=d.get("name", ""),
            description=d.get("description", ""),
            scope=(FederationPolicyScope(d["scope"]) if "scope" in d else FederationPolicyScope.ALL_MEMBERS),
            applies_to_society_ids=tuple(d.get("applies_to_society_ids", [])),
            rules=tuple(d.get("rules", [])),
            enabled=d.get("enabled", True),
            created_at=d.get("created_at", 0.0),
        )


@dataclass(frozen=True)
class Federation:
    """An immutable federation of societies — the domain object (Rule 4).
    Holds no mutation logic itself; every change flows through
    `FederationManager`, which replaces it via `dataclasses.replace()`,
    the same pattern `SocietyRuntime` already uses for `Society`."""

    federation_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    description: str = ""
    member_society_ids: tuple[str, ...] = ()
    policies: tuple[FederationPolicy, ...] = ()
    shared_context_history: tuple[str, ...] = ()
    """Cross-society coordination history — the federation-level analog of
    `Society.coordination_history`, one level up the hierarchy."""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def has_member(self, society_id: str) -> bool:
        return society_id in self.member_society_ids

    def policies_for(self, society_id: str) -> tuple[FederationPolicy, ...]:
        return tuple(p for p in self.policies if p.applies_to(society_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "federation_id": self.federation_id,
            "name": self.name,
            "description": self.description,
            "member_society_ids": list(self.member_society_ids),
            "policies": [p.to_dict() for p in self.policies],
            "shared_context_history": list(self.shared_context_history),
            "metadata": dict(self.metadata),
            "created_at": round(self.created_at, 6),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Federation:
        return cls(
            federation_id=d.get("federation_id", uuid4().hex),
            name=d.get("name", ""),
            description=d.get("description", ""),
            member_society_ids=tuple(d.get("member_society_ids", [])),
            policies=tuple(FederationPolicy.from_dict(p) for p in d.get("policies", [])),
            shared_context_history=tuple(d.get("shared_context_history", [])),
            metadata=dict(d.get("metadata", {})),
            created_at=d.get("created_at", 0.0),
        )


class FederationManager:
    """Manages federations of societies — Planetary Runtime's federation
    MANAGEMENT responsibility (Rule 4: the federation is a domain object,
    the runtime manages it). Mirrors `InteractionManager`/
    `CollectiveLearningEngine`'s shape: owns mutable dict storage, the
    domain object itself stays an immutable value replaced on every
    mutation."""

    def __init__(self) -> None:
        self._federations: dict[str, Federation] = {}

    def create_federation(
        self, name: str, description: str = "", member_society_ids: tuple[str, ...] = ()
    ) -> Federation:
        federation = Federation(
            name=name,
            description=description,
            member_society_ids=member_society_ids,
        )
        self._federations[federation.federation_id] = federation
        return federation

    def get_federation(self, federation_id: str) -> Federation | None:
        return self._federations.get(federation_id)

    def all_federations(self) -> tuple[Federation, ...]:
        return tuple(self._federations.values())

    def federations_for_society(self, society_id: str) -> tuple[Federation, ...]:
        return tuple(f for f in self._federations.values() if f.has_member(society_id))

    def add_member(self, federation_id: str, society_id: str) -> Federation | None:
        federation = self._federations.get(federation_id)
        if federation is None:
            return None
        if federation.has_member(society_id):
            return federation
        updated = dataclasses.replace(
            federation,
            member_society_ids=federation.member_society_ids + (society_id,),
        )
        self._federations[federation_id] = updated
        return updated

    def remove_member(self, federation_id: str, society_id: str) -> Federation | None:
        federation = self._federations.get(federation_id)
        if federation is None:
            return None
        updated = dataclasses.replace(
            federation,
            member_society_ids=tuple(s for s in federation.member_society_ids if s != society_id),
        )
        self._federations[federation_id] = updated
        return updated

    def add_policy(self, federation_id: str, policy: FederationPolicy) -> Federation | None:
        federation = self._federations.get(federation_id)
        if federation is None:
            return None
        updated = dataclasses.replace(federation, policies=federation.policies + (policy,))
        self._federations[federation_id] = updated
        return updated

    def record_cross_society_event(self, federation_id: str, description: str) -> Federation | None:
        """Append a cross-society coordination event to the federation's own
        history. Callers that also want this visible in a live Context
        Stream (e.g. `PlanetaryRuntime`) publish there separately — this
        manager has no Context Stream dependency of its own, so it stays
        usable standalone/in tests without constructing a whole runtime."""
        federation = self._federations.get(federation_id)
        if federation is None:
            return None
        updated = dataclasses.replace(
            federation,
            shared_context_history=federation.shared_context_history + (description,),
        )
        self._federations[federation_id] = updated
        return updated
