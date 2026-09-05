"""Explicit edge decision states (Section 8) + the combined execution
assessment that keeps CONNECTIVITY, POLICY_FRESHNESS, WORLD_STATE_FRESHNESS
and AUTHORITY_FRESHNESS as four separate dimensions (Section 3).

Section 3's own example must hold:

    CONNECTED + STALE_POLICY

is a valid, expected state -- reachability of the control plane says
nothing about whether the LAST policy this node fetched is still current.
`offline_safety.py::ConnectivityStatus` answers "can I reach the control
plane" and `freshness.py::classify_freshness` already independently
answers "is this cached thing still trustworthy" for the plane it is
told to look at (policy, world state, or authority/delegation) -- this
module never re-implements either; it only composes their outputs into
one assessment a caller (or telemetry) can read as a whole, and it never
changes classify_freshness's own logic or thresholds.

EdgeDecisionState (Section 8) reuses GovernanceOrigin's LOCAL/CENTRAL/
ESCALATED distinction where it already applies (kernel/edge/
local_governance.py) rather than inventing a second taxonomy; it adds
only the finer-grained REASON a decision landed where it did, which
GovernanceOrigin alone does not carry.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.monkey_brain.kernel.edge.freshness import Freshness
from src.monkey_brain.kernel.pipeline.offline_safety import ConnectivityStatus


class EdgeDecisionState(Enum):
    LOCAL_ALLOW = "LOCAL_ALLOW"
    LOCAL_DENY = "LOCAL_DENY"
    LOCAL_HUMAN_APPROVAL_REQUIRED = "LOCAL_HUMAN_APPROVAL_REQUIRED"
    """A cached decision itself demands human approval -- never
    satisfiable locally (Section 5: delegation/negotiation/local
    governance are never approval); this state ALWAYS escalates."""
    ESCALATE_POLICY = "ESCALATE_POLICY"
    """No confidently-fresh POLICY is available locally (missing,
    expired, or wrong authority epoch) -- distinct from ESCALATE_AUTHORITY,
    which is about delegation/authority-chain freshness, not policy."""
    ESCALATE_AUTHORITY = "ESCALATE_AUTHORITY"
    """No locally-valid authority (cached snapshot or delegation chain)
    covers this exact (principal, action, resource) at all."""
    ESCALATE_FRESHNESS = "ESCALATE_FRESHNESS"
    """Something relevant IS cached, but it is stale past what this
    specific operation's freshness_requirement tolerates."""
    ESCALATE_COORDINATION = "ESCALATE_COORDINATION"
    """Local negotiation (kernel/edge/negotiation.py) cannot settle this
    term itself -- e.g. a NegotiationError for a forbidden term key --
    and coordination with the counterparty/control plane is required."""
    OFFLINE_DENY = "OFFLINE_DENY"
    """No edge governance layer is configured/consulted at all (or the
    connectivity gate refused with no local substitute available) -- the
    conservative, pre-existing "just say no" behavior action_executor.py
    already had before edge governance existed."""


@dataclass(frozen=True)
class EdgeExecutionAssessment:
    """The combined, explicit view Section 3 requires. Each dimension is
    independent -- CONNECTED does not imply POLICY_FRESHNESS is FRESH,
    and FRESH world state does not imply fresh policy or authority. A
    caller (or dashboard) must look at all four, never infer one from
    another."""
    connectivity: ConnectivityStatus
    policy_freshness: Freshness
    world_state_freshness: Freshness
    authority_freshness: Freshness

    @property
    def fully_healthy(self) -> bool:
        """True only when EVERY dimension is favorable. Section 3's own
        warning example -- CONNECTED + POLICY_STALE + WORLD_STATE_FRESH --
        must NOT read as healthy, and does not: policy_freshness alone
        being anything other than FRESH makes this False regardless of
        connectivity or world-state freshness."""
        return (
            self.connectivity == ConnectivityStatus.CONNECTED
            and self.policy_freshness == Freshness.FRESH
            and self.world_state_freshness in (Freshness.FRESH, Freshness.STALE_BUT_USABLE)
            and self.authority_freshness == Freshness.FRESH
        )

    def to_dict(self) -> dict:
        return {
            "connectivity": self.connectivity.value,
            "policy_freshness": self.policy_freshness.value,
            "world_state_freshness": self.world_state_freshness.value,
            "authority_freshness": self.authority_freshness.value,
            "fully_healthy": self.fully_healthy,
        }


def assess_edge_execution(
    *, connectivity: ConnectivityStatus, policy_freshness: Freshness,
    world_state_freshness: Freshness, authority_freshness: Freshness,
) -> EdgeExecutionAssessment:
    """Pure composition -- every input is computed by its own existing
    authority (assess_connectivity for connectivity, classify_freshness
    for each of the other three against ITS OWN CacheProvenance); this
    function performs no I/O and makes no decision of its own."""
    return EdgeExecutionAssessment(
        connectivity=connectivity, policy_freshness=policy_freshness,
        world_state_freshness=world_state_freshness, authority_freshness=authority_freshness,
    )
