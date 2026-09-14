"""Runtime Agreements — explicit, revocable contracts for knowledge exchange.

No knowledge flows between runtimes without an active agreement.
Agreements are signed, versioned, and auditable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.agreements")


@dataclass
class Agreement:
    """An explicit contract between two runtimes governing knowledge exchange.

    Attributes:
        agreement_id:     unique identifier
        participants:     list of runtime_ids
        knowledge_permissions: what knowledge kinds may flow
        capability_permissions: what capabilities may be invoked
        policy_policies:  what policies apply
        policy_inheritance: whether child agreements inherit parent policies
        duration:         how long the agreement lasts (0 = indefinite)
        renewal:          auto-renewal policy
        revoked:          revocation status
        revoked_at:       when revoked
        revoked_by:       who revoked
        created_at:       creation timestamp
        expires_at:       expiration timestamp
        signed_by:        which runtime signed
        signature:        cryptographic signature
    """

    agreement_id: str = field(default_factory=lambda: str(uuid4()))
    participants: list[str] = field(default_factory=list)
    knowledge_permissions: set[str] = field(default_factory=set)
    capability_permissions: set[str] = field(default_factory=set)
    policy_scope: set[str] = field(default_factory=set)
    policy_inheritance: bool = True
    duration: float = 0.0  # seconds, 0 = indefinite
    renewal: str = "manual"  # manual | auto | never
    revoked: bool = False
    revoked_at: float = 0.0
    revoked_by: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    signed_by: str = ""
    signature: str = ""
    provenance: list[tuple] = field(default_factory=list)

    def __post_init__(self):
        if self.duration > 0 and self.expires_at == 0:
            self.expires_at = self.created_at + self.duration

    @property
    def is_active(self) -> bool:
        if self.revoked:
            return False
        if self.expires_at > 0 and time.time() > self.expires_at:
            return False
        return True

    @property
    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() > self.expires_at

    def covers_knowledge(self, kind: str) -> bool:
        if not self.knowledge_permissions:
            return True
        return kind in self.knowledge_permissions

    def covers_capability(self, cap: str) -> bool:
        if not self.capability_permissions:
            return True
        return cap in self.capability_permissions

    def to_dict(self) -> dict[str, Any]:
        return {
            "agreement_id": self.agreement_id,
            "participants": self.participants,
            "knowledge_permissions": sorted(self.knowledge_permissions),
            "capability_permissions": sorted(self.capability_permissions),
            "policy_scope": sorted(self.policy_scope),
            "policy_inheritance": self.policy_inheritance,
            "duration": self.duration,
            "renewal": self.renewal,
            "revoked": self.revoked,
            "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "signed_by": self.signed_by,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Agreement:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class AgreementStore:
    """Manages runtime agreements.  In-memory; swappable for durable storage."""

    def __init__(self) -> None:
        self._agreements: dict[str, Agreement] = {}
        self._by_participant: dict[str, list[str]] = {}

    def register(self, agreement: Agreement) -> str:
        self._agreements[agreement.agreement_id] = agreement
        for p in agreement.participants:
            self._by_participant.setdefault(p, []).append(agreement.agreement_id)
        return agreement.agreement_id

    def get(self, agreement_id: str) -> Agreement | None:
        a = self._agreements.get(agreement_id)
        return a if a and a.is_active else None

    def active_for(self, runtime_id: str) -> list[Agreement]:
        ids = self._by_participant.get(runtime_id, [])
        return [self._agreements[aid] for aid in ids if aid in self._agreements and self._agreements[aid].is_active]

    def covers_knowledge(self, runtime_a: str, runtime_b: str, kind: str) -> Agreement | None:
        """Find an active agreement between A and B that covers this knowledge kind."""
        for aid in self._by_participant.get(runtime_a, []):
            a = self._agreements.get(aid)
            if a and a.is_active and runtime_b in a.participants and a.covers_knowledge(kind):
                return a
        return None

    def covers_capability(self, runtime_a: str, runtime_b: str, cap: str) -> Agreement | None:
        for aid in self._by_participant.get(runtime_a, []):
            a = self._agreements.get(aid)
            if a and a.is_active and runtime_b in a.participants and a.covers_capability(cap):
                return a
        return None

    def revoke(self, agreement_id: str, revoked_by: str = "") -> bool:
        a = self._agreements.get(agreement_id)
        if not a:
            return False
        a.revoked = True
        a.revoked_at = time.time()
        a.revoked_by = revoked_by
        a.provenance.append(("revoke", revoked_by, round(time.time(), 3)))
        return True

    def cleanup_expired(self) -> int:
        """Remove expired agreements.  Returns count removed."""
        expired = [aid for aid, a in self._agreements.items() if a.is_expired]
        for aid in expired:
            del self._agreements[aid]
        return len(expired)

    def list_all(self) -> list[Agreement]:
        return list(self._agreements.values())


_default_store: AgreementStore | None = None


def get_agreement_store() -> AgreementStore:
    global _default_store
    if _default_store is None:
        _default_store = AgreementStore()
    return _default_store
