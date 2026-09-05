"""Delegation — temporary delegation of authority from one membership to
an actor (Membership as a First-Class Runtime Resource refactor).

A new, lightweight, dependency-free model, distinct from THREE existing
mechanisms in this codebase that model different situations:
kernel/domains/domain_security.py's grant/revoke/check_delegation (real,
working, but Neo4j-KG-backed and written for grocery/household semantics
specifically), kernel/compile/trust.py::TrustEdge.delegation_chain
(transitive delegation across the runtime-to-runtime institutional trust
layer — a different layer entirely), and — most important to keep
straight, added later — kernel/delegation.py::DelegationCredential
(cryptographically signed, Ed25519, attenuation-chain-verified authority
used at the ensure_governed/OPA boundary to gate real capability
EXECUTION). None of the three is touched by this refactor.

THIS module's `Delegation`/`DelegationRegistry` carries NO cryptographic
proof and grants NO execution authority whatsoever — its only real,
live consumer is kernel/affiliations/graph.py's communication-routing
cascade (`_delegated_authority`), deciding whether two actors may
EXCHANGE MESSAGES at all, never whether a capability may execute. Do not
wire `DelegationRegistry.is_valid()`/`effective_delegated_permissions()`
into any capability-authorization decision — that is exclusively
kernel/delegation.py::verify_delegation_chain's job, via ensure_governed.
Sharing the name "delegation" across these mechanisms is a known,
accepted naming collision (see docs/CLOUD_EDGE_ACTOR_ARCHITECTURE.md's
Society architecture review), not an indication they are interchangeable.

In-memory dict-backed — delegations are few and lightweight per actor, so
no pluggable-backend machinery (unlike kernel/timeline/store.py's
TimelineStore) is needed here.
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Delegation:
    delegation_id: str = field(default_factory=lambda: uuid4().hex)
    membership_id: str = ""
    """The delegator's membership — delegated authority is scoped to
    whatever that membership's own permissions/roles allow."""
    delegate_actor_id: str = ""
    permissions: tuple[str, ...] = ()
    valid_from: float = field(default_factory=time.time)
    valid_until: float | None = None
    """None = no expiry (still subject to explicit revoke())."""
    constraints: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    revoked: bool = False
    revoked_at: float | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "delegation_id": self.delegation_id, "membership_id": self.membership_id,
            "delegate_actor_id": self.delegate_actor_id, "permissions": list(self.permissions),
            "valid_from": self.valid_from, "valid_until": self.valid_until,
            "constraints": dict(self.constraints), "reason": self.reason,
            "revoked": self.revoked, "revoked_at": self.revoked_at, "created_at": self.created_at,
        }


class DelegationRegistry:
    """Grants/revokes/queries Delegations. Each grant/revoke also appends a
    MembershipRecord timeline event (via the membership_registry passed
    in) — see kernel/society/membership.py::SocietyMembershipRegistry —
    satisfying "every membership event creates an immutable timeline
    entry" without a second event-log mechanism."""

    def __init__(self, membership_registry: Any = None) -> None:
        self._membership_registry = membership_registry
        self._delegations: dict[str, Delegation] = {}

    def grant(
        self, membership_id: str, delegate_actor_id: str, permissions: tuple[str, ...],
        valid_until: float | None = None, constraints: dict[str, Any] | None = None, reason: str = "",
    ) -> Delegation:
        delegation = Delegation(
            membership_id=membership_id, delegate_actor_id=delegate_actor_id,
            permissions=tuple(permissions), valid_until=valid_until,
            constraints=dict(constraints or {}), reason=reason,
        )
        self._delegations[delegation.delegation_id] = delegation
        if self._membership_registry is not None:
            self._membership_registry.record_event(
                membership_id, "delegation_granted", delegation_id=delegation.delegation_id,
            )
        return delegation

    def revoke(self, delegation_id: str, reason: str = "") -> bool:
        delegation = self._delegations.get(delegation_id)
        if delegation is None or delegation.revoked:
            return False
        revoked = dataclasses.replace(
            delegation, revoked=True, revoked_at=time.time(), reason=reason or delegation.reason,
        )
        self._delegations[delegation_id] = revoked
        if self._membership_registry is not None:
            self._membership_registry.record_event(
                delegation.membership_id, "delegation_revoked", delegation_id=delegation_id,
            )
        return True

    def get(self, delegation_id: str) -> Delegation | None:
        return self._delegations.get(delegation_id)

    def list_for_membership(self, membership_id: str) -> tuple[Delegation, ...]:
        return tuple(d for d in self._delegations.values() if d.membership_id == membership_id)

    def is_valid(self, delegation_id: str) -> bool:
        delegation = self._delegations.get(delegation_id)
        if delegation is None or delegation.revoked:
            return False
        now = time.time()
        if now < delegation.valid_from:
            return False
        if delegation.valid_until is not None and now > delegation.valid_until:
            return False
        return True

    def effective_delegated_permissions(self, delegate_actor_id: str) -> tuple[str, ...]:
        permissions: list[str] = []
        for delegation in self._delegations.values():
            if delegation.delegate_actor_id == delegate_actor_id and self.is_valid(delegation.delegation_id):
                permissions.extend(delegation.permissions)
        return tuple(dict.fromkeys(permissions))
