"""Offline / disconnected-edge safety classification (Cloud/Edge Actor
Convergence, Section 11/31).

An Actor's execution location is infrastructure — the SAME Actor may run
on a fully-connected cloud node or an intermittently-connected edge node.
What changes is not the Actor's cognition, authority, or capability
semantics; it is whether the substrate a given operation needs is
currently reachable. This module answers exactly one question, honestly:
*given what's currently reachable, is it safe to actually run this
capability right now* — never *what should the Actor do about it*
(that remains the Actor's own planning) and never *is this capability
allowed* (that remains TransitionGate/domain_security.py, unchanged and
untouched by this module).

Do not invent unsafe semantics: the default classification for any
capability this module doesn't explicitly recognize is the conservative
one, REQUIRES_AUTHORITY — an unclassified capability is assumed
consequential until proven otherwise, never assumed safe.

Wired into ActionExecutor via an optional `connectivity_check` hook
(action_executor.py) — None (the default) preserves the exact prior
behavior for every existing caller; this only takes effect for a caller
that explicitly opts in (the edge runtime, kernel/society/edge_runtime.py).
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any

logger = logging.getLogger("agentos.pipeline.offline_safety")


class ConnectivityStatus(Enum):
    """What substrate this process can currently reach. Recomputed on
    demand (assess_connectivity), never cached indefinitely — a process
    that regains connectivity must be able to notice within one check,
    not stay stuck DISCONNECTED forever."""

    CONNECTED = "connected"
    """Redis (the Actor Registry / lease / checkpoint substrate this
    whole control plane depends on) is reachable. NATS/Mongo may or may
    not be — see DEGRADED."""
    DEGRADED = "degraded"
    """Redis is reachable (identity, lease, checkpoint, and world-tensor
    persistence all still function), but NATS and/or Mongo/the shared
    Knowledge Graph is not — cross-actor messaging or durable belief
    history may be stale or unavailable, but local cognition and
    already-cached world state remain usable."""
    DISCONNECTED = "disconnected"
    """Redis itself is unreachable. This is the authoritative substrate
    for actor identity, lease ownership, and desired/observed placement
    — without it, this process cannot safely claim to speak for the
    Actor's authoritative state at all."""


class OperationSafety(Enum):
    """Section 11's explicit classification. Every capability falls into
    exactly one bucket."""

    SAFE_OFFLINE = "safe_offline"
    """Local observation/cognition that only touches this Actor's own
    local belief — never blocked, any connectivity status."""
    REQUIRES_WORLD_STATE = "requires_world_state"
    """Needs current, authoritative shared world/Knowledge Graph state
    (e.g. real-time stock/price) — blocked when DISCONNECTED, allowed
    when CONNECTED or DEGRADED (DEGRADED still has Redis-backed world
    access; only NATS/Mongo-specific freshness may be stale, which is a
    quality concern, not a safety one)."""
    REQUIRES_AUTHORITY = "requires_authority"
    """Consequential — mutates shared state, moves value, or commits the
    Actor to a delegation. Blocked unless CONNECTED. The conservative
    default for anything not explicitly classified otherwise."""
    REQUIRES_SYNC = "requires_sync"
    """Needs a completed synchronization round-trip with the cloud layer
    before it is safe to proceed (e.g. an edge-cached reservation that
    must be confirmed against the authoritative inventory first) —
    treated identically to REQUIRES_AUTHORITY for gating purposes (blocked
    unless CONNECTED), kept as a distinct label because the operator-
    facing reason differs ("needs a sync", not "needs authority")."""


# Explicit capability -> safety classification. Names match the real
# capability classes registered on the CapabilityBus (kernel/domains/*.py)
# — grep-verified against grocery.py/commerce.py at the time this was
# written; a capability not listed here gets the conservative default
# (REQUIRES_AUTHORITY), never SAFE_OFFLINE by omission.
_SAFE_OFFLINE = frozenset(
    {
        "AnswerQuestionCapability",
        "AskActorCapability",
        "ObserveCapability",
        "GetStatusCapability",
        "SummarizeCapability",
        "RecallMemoryCapability",
        "GetActorStatusCapability",
        "LocalObservationCapability",
    }
)
_REQUIRES_WORLD_STATE = frozenset(
    {
        "ProductSelectionCapability",
        "PriceCheckCapability",
        "InventoryCheckCapability",
        "CheckStockCapability",
        "SearchProductsCapability",
        "GetProductDetailsCapability",
        "CompareOffersCapability",
    }
)
_REQUIRES_SYNC = frozenset(
    {
        "ReserveStockCapability",
        "ReserveInventoryCapability",
    }
)
_REQUIRES_AUTHORITY = frozenset(
    {
        "OrderCreationCapability",
        "PaymentCapability",
        "RefundCapability",
        "DeliveryCapability",
        "DelegationCapability",
        "NegotiationCapability",
        "CancelOrderCapability",
        "WalletCapability",
        "RazorpayWalletCapability",
        "TransferFundsCapability",
        "GrantPermissionCapability",
        "RevokePermissionCapability",
    }
)

_WAITING_FOR_WORLD_STATE = "WAITING_FOR_WORLD_STATE"
_WAITING_FOR_AUTHORITY = "WAITING_FOR_AUTHORITY"
_DISCONNECTED = "DISCONNECTED"


def classify_capability(capability_name: str) -> OperationSafety:
    if capability_name in _SAFE_OFFLINE:
        return OperationSafety.SAFE_OFFLINE
    if capability_name in _REQUIRES_WORLD_STATE:
        return OperationSafety.REQUIRES_WORLD_STATE
    if capability_name in _REQUIRES_SYNC:
        return OperationSafety.REQUIRES_SYNC
    if capability_name in _REQUIRES_AUTHORITY:
        return OperationSafety.REQUIRES_AUTHORITY
    # Conservative default (module docstring): unclassified is assumed
    # consequential, never assumed safe.
    return OperationSafety.REQUIRES_AUTHORITY


def assess_connectivity(planetary_runtime: Any) -> ConnectivityStatus:
    """Cheap, synchronous, no-side-effect check of what this process can
    currently reach. Never raises — an unreachable dependency is exactly
    the condition being tested for, not an error in this function."""
    redis = getattr(planetary_runtime, "_redis", None)
    if redis is None:
        return ConnectivityStatus.DISCONNECTED
    try:
        redis.ping()
    except Exception as exc:
        logger.debug("assess_connectivity: Redis ping failed: %s", exc)
        return ConnectivityStatus.DISCONNECTED

    nats = getattr(planetary_runtime, "_nats_client", None)
    nats_ok = nats is not None and bool(getattr(nats, "is_connected", True))
    if not nats_ok:
        return ConnectivityStatus.DEGRADED
    return ConnectivityStatus.CONNECTED


def check_operation_allowed(capability_name: str, connectivity: ConnectivityStatus) -> tuple[bool, str, str]:
    """Returns (allowed, waiting_state, reason). waiting_state is one of
    "" (allowed), WAITING_FOR_WORLD_STATE, WAITING_FOR_AUTHORITY,
    DISCONNECTED (Section 31's exact vocabulary) — never a silent
    execution of a consequential action against a stale/absent
    authoritative state."""
    safety = classify_capability(capability_name)

    if safety == OperationSafety.SAFE_OFFLINE:
        return True, "", ""

    if connectivity == ConnectivityStatus.DISCONNECTED:
        return (
            False,
            _DISCONNECTED,
            (
                f"{capability_name} requires {safety.value} but this node cannot reach "
                "the authoritative Actor Registry/lease substrate (Redis unreachable)"
            ),
        )

    if safety == OperationSafety.REQUIRES_WORLD_STATE:
        # DEGRADED still has Redis-backed world/KG access -- only NATS/
        # Mongo freshness may be stale, a quality concern, not a safety
        # one (module docstring).
        return True, "", ""

    # REQUIRES_AUTHORITY / REQUIRES_SYNC: only proceed when fully CONNECTED.
    if connectivity == ConnectivityStatus.CONNECTED:
        return True, "", ""

    waiting_state = _WAITING_FOR_AUTHORITY
    return (
        False,
        waiting_state,
        (
            f"{capability_name} requires {safety.value} but this node is only "
            f"{connectivity.value} (NATS/Mongo unreachable) — refusing to execute a "
            "consequential action without full connectivity to authoritative state"
        ),
    )


def make_connectivity_check(planetary_runtime: Any, *, cache_seconds: float = 5.0):
    """Build the `connectivity_check` callable ActionExecutor's optional
    hook expects: `Callable[[str], tuple[bool, str, str]]`. Connectivity
    is reassessed at most once per `cache_seconds` (default 5s) rather
    than on every single capability call within one tick — cheap either
    way (a Redis PING), but a tick can invoke several capabilities in a
    row and each one re-pinging is unnecessary churn."""
    state: dict[str, Any] = {"status": None, "checked_at": 0.0}

    def _connectivity_check(capability_name: str) -> tuple[bool, str, str]:
        now = time.time()
        if state["status"] is None or (now - state["checked_at"]) > cache_seconds:
            state["status"] = assess_connectivity(planetary_runtime)
            state["checked_at"] = now
        return check_operation_allowed(capability_name, state["status"])

    return _connectivity_check
