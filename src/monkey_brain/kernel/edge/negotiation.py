"""Local Negotiation — resource/capability/constraint/reservation/
delegation-aware negotiation protocols that do not require a centralized
round trip.

The single invariant this whole module exists to enforce:

    delegation != approval
    negotiation != authorization

A successful negotiation produces an `Agreement` — a proposed, bounded
commitment both sides accept — never authority to execute it. Whatever
capability eventually acts on that agreement still goes through the
normal governance boundary (kernel/security_boundary.py::ensure_governed,
locally via kernel/edge/local_governance.py or centrally) exactly like
any other action. This module has no way to invoke a capability at all,
by design — it cannot bypass governance because it has no path to a side
effect.

Reuses kernel/domains/grocery.py's existing negotiation vocabulary
(NegotiatePriceCapability/NegotiateTermsCapability already establish
"negotiation produces a proposed term, not an executed one" for the
grocery vertical) rather than inventing a competing shape -- this is the
vertical-agnostic version of the same idea, usable before a capability
bus even exists (e.g. a robot deciding a reservation with another robot
actor with no cloud round trip).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class NegotiationKind(Enum):
    RESOURCE_REQUEST = "resource_request"
    CAPABILITY_NEGOTIATION = "capability_negotiation"
    CONSTRAINT_NEGOTIATION = "constraint_negotiation"
    RESERVATION_NEGOTIATION = "reservation_negotiation"
    DELEGATION_AWARE_NEGOTIATION = "delegation_aware_negotiation"
    EXECUTION_COMMITMENT = "execution_commitment"


class AgreementStatus(Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Agreement:
    """A proposed, bounded commitment -- NOT authority. `terms` must never
    contain a key like "authorized"/"approved"/"allow" -- see
    NegotiationEngine.propose's own guard. Consuming this to actually
    execute anything still requires an independent governance decision
    (ensure_governed, locally or centrally) keyed off the SAME
    capability/resource/constraints named here, not off this agreement's
    mere existence."""
    agreement_id: str = field(default_factory=lambda: uuid4().hex)
    kind: NegotiationKind = NegotiationKind.RESOURCE_REQUEST
    initiator: str = ""
    counterparty: str = ""
    capability: str = ""
    resource: str = ""
    terms: dict[str, Any] = field(default_factory=dict)
    status: AgreementStatus = AgreementStatus.PROPOSED
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    delegation_id: str = ""
    """The delegation (kernel/delegation.py) whose already-attenuated
    scope/constraints this negotiation stayed within, when the
    negotiation is delegation-aware. Empty when the initiator negotiated
    under its own root authority."""

    @property
    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() > self.expires_at


_FORBIDDEN_TERM_KEYS = frozenset({
    "authorized", "approved", "allow", "allowed", "governance_allowed",
    "opa_allow", "approval_mode", "human_approved",
})


class NegotiationError(Exception):
    pass


class LocalNegotiationEngine:
    """Consumes trusted local policy/delegation state (a verified
    DelegationCredential or delegation chain, and optionally the same
    SignedPolicySnapshot local_governance.py checks) to decide what an
    agreement's terms are ALLOWED to contain -- never whether the
    resulting action is authorized to run. That question belongs
    entirely to governance, evaluated separately and later."""

    def propose(
        self, *, kind: NegotiationKind, initiator: str, counterparty: str,
        capability: str, resource: str, terms: dict[str, Any],
        ttl_seconds: float = 120.0, delegation: Any = None,
    ) -> Agreement:
        for key in terms:
            if key.lower() in _FORBIDDEN_TERM_KEYS:
                raise NegotiationError(
                    f"negotiation terms cannot carry an authorization-shaped key {key!r} "
                    "-- negotiation never grants authority, only governance does",
                )
        if delegation is not None:
            self._require_terms_within_delegation(terms, delegation)
        return Agreement(
            kind=kind, initiator=initiator, counterparty=counterparty,
            capability=capability, resource=resource, terms=dict(terms),
            expires_at=time.time() + ttl_seconds,
            delegation_id=getattr(delegation, "delegation_id", ""),
        )

    def accept(self, agreement: Agreement) -> Agreement:
        if agreement.is_expired:
            return self._with_status(agreement, AgreementStatus.EXPIRED)
        return self._with_status(agreement, AgreementStatus.ACCEPTED)

    def reject(self, agreement: Agreement, *, reason: str = "") -> Agreement:
        return self._with_status(agreement, AgreementStatus.REJECTED)

    @staticmethod
    def _with_status(agreement: Agreement, status: AgreementStatus) -> Agreement:
        from dataclasses import replace
        return replace(agreement, status=status)

    @staticmethod
    def _require_terms_within_delegation(terms: dict[str, Any], delegation: Any) -> None:
        """Constraint-by-constraint check reusing kernel/delegation.py's
        OWN conservative narrowing logic -- never a second, competing
        interpretation of what "within delegated authority" means."""
        from src.monkey_brain.kernel.delegation import _constraints_are_narrower_or_equal

        constraints = getattr(delegation, "constraints", None)
        if constraints is None:
            return
        if not _constraints_are_narrower_or_equal(terms, constraints):
            raise NegotiationError(
                "proposed negotiation terms exceed the initiator's delegated constraints",
            )


def agreement_is_executable_now(agreement: Agreement) -> tuple[bool, str]:
    """A pure, local readiness check -- NOT a governance decision. True
    only means "this agreement is not expired and was actually accepted
    by both sides"; the caller must still pass the resulting action
    through ensure_governed (locally or centrally) before invoking
    anything. Named distinctly from any *_allowed/*_authorized helper
    elsewhere in this codebase so it is never mistaken for one."""
    if agreement.status != AgreementStatus.ACCEPTED:
        return False, f"agreement is {agreement.status.value}, not accepted"
    if agreement.is_expired:
        return False, "agreement has expired"
    return True, "agreement is ready for a governed execution attempt"
