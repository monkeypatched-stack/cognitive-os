"""Local Governance — evaluate ALREADY-ISSUED authority at the edge,
without a live round trip to OPA/GovernanceEngine.

This is explicitly NOT a second authorization model. It never invents a
policy decision: it only checks whether a signed, control-plane-issued
verdict (kernel/edge/policy_cache.py) and, when present, a verified
delegation chain (kernel/delegation.py) still cover the exact request in
front of it, right now. Anything this cannot establish with confidence
-- an unknown capability, an expired/superseded snapshot, a delegation
that doesn't verify, or a decision that requires human approval --
ESCALATES (returns LocalGovernanceOutcome.escalate=True) rather than
guessing. The caller (kernel/pipeline/action_executor.py) is responsible
for actually contacting the control plane when escalation is required;
this module never does that itself.

    Central control plane:  authoritative policy and authority
    Edge governance:         local evaluation of already-issued authority

HUMAN_APPROVAL_REQUIRED can never be satisfied locally -- Section 5's own
invariant ("delegation != approval", and by the same logic, a cached
policy snapshot != a human's approval). A snapshot whose approval_mode is
HUMAN_APPROVAL_REQUIRED always escalates, even if otherwise fresh and
valid, because satisfying it requires contacting whatever human-approval
mechanism (ApprovalArtifact) the control plane owns -- an edge node has
no authority to manufacture that decision itself.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from src.monkey_brain.kernel.edge.decision_state import EdgeDecisionState
from src.monkey_brain.kernel.edge.freshness import Freshness
from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache

logger = logging.getLogger("agentos.edge.local_governance")


class GovernanceOrigin(Enum):
    """Section 13's observability vocabulary for where a decision
    actually came from."""
    LOCAL = "LOCAL"
    CENTRAL = "CENTRAL"
    ESCALATED = "ESCALATED"


@dataclass(frozen=True)
class LocalGovernanceOutcome:
    allowed: bool
    escalate: bool
    """True whenever this evaluator could not establish a confident
    local decision -- the caller must contact the control plane rather
    than treat `allowed=False` as a final DENY. allowed and escalate are
    never both True: an escalation is never also a local ALLOW."""
    reason: str
    origin: GovernanceOrigin
    policy_rule: str = ""
    authority_epoch: int = 0
    snapshot_id: str = ""
    delegation_id: str = ""
    decision_state: EdgeDecisionState = EdgeDecisionState.OFFLINE_DENY

    def __post_init__(self) -> None:
        if self.allowed and self.escalate:
            raise ValueError("a governance outcome cannot both allow and escalate")


def _deny_local(reason: str, *, decision_state: EdgeDecisionState = EdgeDecisionState.LOCAL_DENY, **kwargs: Any) -> LocalGovernanceOutcome:
    return LocalGovernanceOutcome(allowed=False, escalate=False, reason=reason, origin=GovernanceOrigin.LOCAL, decision_state=decision_state, **kwargs)


def _escalate(reason: str, *, decision_state: EdgeDecisionState = EdgeDecisionState.ESCALATE_AUTHORITY, **kwargs: Any) -> LocalGovernanceOutcome:
    return LocalGovernanceOutcome(allowed=False, escalate=True, reason=reason, origin=GovernanceOrigin.ESCALATED, decision_state=decision_state, **kwargs)


def _allow_local(reason: str, **kwargs: Any) -> LocalGovernanceOutcome:
    return LocalGovernanceOutcome(allowed=True, escalate=False, reason=reason, origin=GovernanceOrigin.LOCAL, decision_state=EdgeDecisionState.LOCAL_ALLOW, **kwargs)


class LocalGovernanceEvaluator:
    """Consulted by ActionExecutor (via kernel/pipeline/offline_safety.py's
    connectivity gate) ONLY when the control plane is not reachable
    (DEGRADED/DISCONNECTED) for a capability that would otherwise be
    unconditionally refused. When CONNECTED, ensure_governed's own live
    _authorize() call remains the path of record -- this evaluator is
    never consulted at all in that case, so it can never become a
    competing, always-on authorization system."""

    def __init__(
        self, policy_cache: EdgePolicyCache, *,
        current_authority_epoch_fn: Callable[[], int] | None = None,
    ) -> None:
        self._policy_cache = policy_cache
        # A callable, not a stored int: the local runtime's own
        # last-synced epoch (kernel/edge/sync.py) can advance between
        # calls without requiring this evaluator to be reconstructed.
        self._current_authority_epoch_fn = current_authority_epoch_fn or (lambda: 0)

    def evaluate(
        self, *, principal: str, action: str, resource: str, authenticated_principal: str,
        audience: str = "", delegation_chain: tuple[Any, ...] = (),
        now: float | None = None,
    ) -> LocalGovernanceOutcome:
        """`delegation_chain` (kernel/delegation.py::DelegationCredential
        tuple, root-first), when the request claims to run under
        delegated (not the caller's own root) authority. Verified with
        the SAME kernel/delegation.py::verify_delegation_chain used
        everywhere else in this codebase -- never a second delegation
        verifier."""
        now = time.time() if now is None else now
        current_epoch = self._current_authority_epoch_fn()

        if delegation_chain:
            delegation_result = self._verify_delegation(
                delegation_chain, authenticated_delegate=authenticated_principal, now=now,
            )
            if not delegation_result.authorized:
                return _deny_local(
                    f"delegation does not verify locally: {delegation_result.failure_reason}",
                )

        snapshot, freshness, reason = self._policy_cache.get_valid(
            principal=principal, action=action, resource=resource,
            authenticated_principal=authenticated_principal, audience=audience,
            current_authority_epoch=current_epoch,
        )
        if snapshot is None:
            return _escalate(
                f"no locally-valid authority for this request: {reason}", authority_epoch=current_epoch,
                decision_state=EdgeDecisionState.ESCALATE_POLICY,
            )

        if snapshot.approval_mode == "DENY":
            # A DENY snapshot IS a confident local decision -- the
            # control plane already decided this, and a DENY cannot be
            # made stale-but-usable into an ALLOW by any local reasoning.
            return _deny_local(
                "cached authority denies this operation",
                policy_rule=snapshot.policy_rule, authority_epoch=snapshot.authority_epoch,
                snapshot_id=snapshot.snapshot_id,
            )

        if snapshot.approval_mode == "HUMAN_APPROVAL_REQUIRED":
            # Never locally satisfiable -- see module docstring.
            return _escalate(
                "cached authority requires human approval, which cannot be obtained locally",
                policy_rule=snapshot.policy_rule, authority_epoch=snapshot.authority_epoch,
                snapshot_id=snapshot.snapshot_id,
                decision_state=EdgeDecisionState.LOCAL_HUMAN_APPROVAL_REQUIRED,
            )

        if freshness == Freshness.STALE_BUT_USABLE:
            # AUTO_APPROVE, verified, but past strict expiry within the
            # freshness_requirement's own grace window. requires_authority's
            # grace window is 0 (freshness.py) so in practice a snapshot
            # never reaches this branch as STALE_BUT_USABLE at all -- kept
            # as an explicit, conservative branch rather than silently
            # falling through to ALLOW, in case a future freshness policy
            # ever widens that window for a specific action class.
            return _escalate(
                "cached authority is stale-but-usable, not fresh enough for a local authority decision",
                policy_rule=snapshot.policy_rule, authority_epoch=snapshot.authority_epoch,
                snapshot_id=snapshot.snapshot_id,
                decision_state=EdgeDecisionState.ESCALATE_FRESHNESS,
            )

        return _allow_local(
            "cached, verified, fresh AUTO_APPROVE authority",
            policy_rule=snapshot.policy_rule, authority_epoch=snapshot.authority_epoch,
            snapshot_id=snapshot.snapshot_id,
        )

    @staticmethod
    def _verify_delegation(delegation_chain: tuple[Any, ...], *, authenticated_delegate: str, now: float):
        from src.monkey_brain.kernel.delegation import verify_delegation_chain
        return verify_delegation_chain(
            chain=delegation_chain, authenticated_delegate=authenticated_delegate, now=now,
        )


def to_policy_decision(outcome: LocalGovernanceOutcome) -> dict[str, Any]:
    """Translate a LocalGovernanceOutcome into the exact policy-decision
    shape ensure_governed's `local_policy_decision` parameter expects
    (kernel/security_boundary.py::run_governed_mutation) -- the SAME
    shape _authorize()/GovernanceEngine.evaluate() already produce, so
    downstream ApprovalArtifact creation cannot tell the difference.

    Must NEVER be called on an outcome with escalate=True: an escalation
    is not a policy decision at all, it is an instruction to the caller
    to contact the control plane. Calling this on one would silently
    manufacture a DENY the local evaluator never actually asserted with
    confidence — callers must branch on `outcome.escalate` themselves.
    """
    if outcome.escalate:
        raise ValueError(
            "to_policy_decision() called on an escalation outcome -- "
            "escalate=True means 'contact the control plane', not 'this is a decision'",
        )
    return {
        "allowed": outcome.allowed,
        "approval_mode": "AUTO_APPROVE" if outcome.allowed else "DENY",
        "reason": outcome.reason,
        "policy_rule": outcome.policy_rule,
        "risk_level": "LOW" if outcome.allowed else "HIGH",
        "source": "edge_local_governance",
    }
