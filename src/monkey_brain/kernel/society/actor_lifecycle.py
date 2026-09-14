"""Actor Lifecycle data model — desired state, observed state, and
reconciliation results for the Actor Lifecycle Controller
(actor_lifecycle_controller.py).

This module is deliberately pure data: no Redis, no Mongo, no NATS, no
PlanetaryRuntime reference. The controller module owns all I/O; this
module owns the vocabulary both the controller and its callers (API
routes, tests) share.

Desired state vs. observed state (Deployment Architecture, Section 3/6):

    ActorDesiredState  — what the control plane WANTS for this actor.
                         Durable, small, and deliberately simple: RUNNING,
                         SUSPENDED, or TERMINATED. This is the CognitiveOS
                         analog of a Kubernetes Pod's spec — declarative,
                         not a blow-by-blow of how to get there.

    ActorStatus        — what the actor's own runtime last REPORTED
                         (kernel/society/domain.py — REGISTERED,
                         INITIALIZED, ACTIVE, IDLE, SUSPENDED, FAILED,
                         TERMINATED). This is the analog of Pod.status.

The controller reconciles the two. Transitional moments (STARTING, READY,
SUSPENDING, RESUMING, TERMINATING) are deliberately NOT separately
persisted states — see LifecycleEvent below and the controller's own
module docstring for why: a persisted "currently mid-transition" flag is
itself a piece of state that can desync from reality on a crash, which is
exactly the class of bug this whole component exists to close. Instead,
those moments are observable as an ordered sequence of published events,
and "is a transition currently in flight" is answered by whether this
actor's lease (PlanetaryRuntime.acquire_actor_lease) is currently held —
a single, already-existing, already-correct source of truth, not a
second one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class ActorDesiredState(Enum):
    """What the control plane wants for this actor. Durable
    (PlanetaryRuntime.set_actor_desired_state), independent of whether the
    actor is currently resident in any process's memory — the CognitiveOS
    analog of a Kubernetes Pod/Deployment spec."""

    RUNNING = "running"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


class LifecycleEventType(Enum):
    """One entry per real lifecycle transition the controller performs or
    observes — published to the Context Stream (ContextEventType.
    ACTOR_LIFECYCLE) and to the durable lifecycle history (TimelineStore,
    via audit_trail.record_decision_event — reused, not a new store)."""

    ACTOR_CREATED = "actor_created"
    ACTOR_STARTING = "actor_starting"
    ACTOR_READY = "actor_ready"
    ACTOR_STARTED = "actor_started"
    ACTOR_SUSPENDING = "actor_suspending"
    ACTOR_SUSPENDED = "actor_suspended"
    ACTOR_RESUMING = "actor_resuming"
    ACTOR_RESUMED = "actor_resumed"
    ACTOR_FAILED = "actor_failed"
    ACTOR_RECOVERING = "actor_recovering"
    ACTOR_RECOVERED = "actor_recovered"
    ACTOR_TERMINATING = "actor_terminating"
    ACTOR_TERMINATED = "actor_terminated"
    RECONCILE_SKIPPED = "reconcile_skipped"
    """Reconciliation ran but took no action — either desired already
    matches observed, or the actor's lease was held elsewhere (another
    node/reconciler currently mid-transition). Emitted at debug-metric
    granularity, not published to the Context Stream, to avoid an event
    storm on every reconcile tick for every settled actor (Section 16)."""
    ACTOR_UNSCHEDULABLE = "actor_unschedulable"
    """Desired=RUNNING, actor exists, but the Scheduler found no healthy
    node satisfying its placement requirements — an explicit, valid,
    non-fabricating terminal state (Actor Scheduler spec, Section 11),
    never silently retried into a fake placement."""
    ACTOR_SCHEDULED_ELSEWHERE = "actor_scheduled_elsewhere"
    """The Scheduler placed this actor on a different node than the one
    currently reconciling it — this node correctly takes no local action
    and lets the target node's own reconcile loop pick it up."""


@dataclass(frozen=True)
class LifecycleEvent:
    """One lifecycle transition, in the shape the Context Stream and
    lifecycle-history query both use."""

    actor_id: str
    event_type: LifecycleEventType
    previous_state: str = ""
    new_state: str = ""
    reason: str = ""
    source: str = "ActorLifecycleController"
    correlation_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ObservedActorState:
    """What reconcile() actually finds for one actor_id — merges the
    durable registry record (PlanetaryRuntime.locate_actor, correct
    regardless of which node the actor currently lives on) with this
    process's own local residency, when known."""

    actor_id: str
    exists: bool
    """False = no registry record and not resident anywhere this process
    knows about — i.e. this actor_id was never registered, or has been
    fully terminated and removed."""
    status: str = ""
    """ActorStatus.value, or "" if exists is False."""
    node_id: str = ""
    updated_at: float = 0.0
    is_stale: bool = False
    """True if updated_at is older than the configured staleness
    threshold while desired state is RUNNING — the controller's crash
    signal (Section 12): nothing has reported this actor alive recently."""
    resident_here: bool = False
    """True if this actor is actively ticking in this process."""
    lease_held: bool = False
    """True if some node currently holds this actor's ownership lease —
    i.e. a tick or a reconciliation action is genuinely in flight right
    now, not crashed. Distinguishes "stale because no one has ticked it
    in a while" (real crash signal) from "stale because it's been
    legitimately mid-tick for a long LLM call" (not a crash)."""
    desired_node_id: str = ""
    """The Actor Scheduler's current placement decision for this actor
    (PlanetaryRuntime.get_actor_desired_node), read once per observe()
    call — "" if never scheduled (no placement requirement expressed
    yet; today's implicit single-node behavior). Distinct from node_id
    above, which is where the registry last saw this actor ACTUALLY
    running (observed placement) — this field is the DESIRED placement,
    the same desired-vs-observed split the rest of this module already
    draws for lifecycle state."""


@dataclass(frozen=True)
class ReconciliationResult:
    """What one reconcile(actor_id) call decided and did — returned to
    the caller (API route, test, reconcile_all()) and folded into
    PlanetaryCycleResult-style metrics, never silently swallowed."""

    actor_id: str
    desired_state: str
    observed_before: str
    action: str
    """One of: "none", "start", "resume", "suspend", "terminate",
    "recover", "skipped_lease_held", "skipped_unknown_actor",
    "unschedulable", "scheduled_elsewhere"."""
    succeeded: bool = True
    reason: str = ""
    timestamp: float = field(default_factory=time.time)
