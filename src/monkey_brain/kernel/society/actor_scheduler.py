"""Actor Scheduler — the CognitiveOS Control Plane component that decides
WHERE an Actor executes.

    Actor Desired State
           ↓
    Actor Registry
           ↓
    Actor Scheduler
           ↓
    Execution Node
           ↓
    Actor Runtime

Central invariant: ACTOR IDENTITY ≠ ACTOR LOCATION. The Scheduler produces
an Actor → Execution Node assignment; it never decides what an Actor
thinks, is authorized to do, or does in the world. Those remain the
Actor's own cognition (unmodified) and CognitiveOS governance
(TransitionGate/domain_security.py, unmodified) — this module never
imports either.

Relationship to kernel/distributed/edge_device_coordinator.py: that
module (EdgeDevice/DeviceCluster/DistributedExecutionCoordinator) already
explored this problem — capacity-based device placement, region
clustering, a DeviceType enum — but confirmed, by exhaustive grep, to
have zero live callers anywhere in this codebase. It is also
architecturally disconnected from the real actor model: its own
`DistributedActor` mixin (a parallel `self.id`/`self._device_id` bag)
never touches `ActorRuntimeState`/`ActorIdentity`, and its coordinator
holds all state in plain process-local dicts with no persistence at all
— exactly the single-process assumption this session's Actor Registry
work closed everywhere else. Rather than build the real, integrated
scheduler as a thin wrapper around a disconnected, unpersisted prototype,
this module reuses its GOOD IDEAS (a node-class enum, explicit
capacity/available-capacity, capability-based filtering) as fresh,
Redis-backed, PlanetaryRuntime-integrated code. See docs/ACTOR_SCHEDULER.md
for the full current-state assessment.

Algorithm (Section 9): deterministic, explainable, reproducible. No ML,
no LLM. candidates = healthy nodes -> filter hard constraints -> rank
preferences -> select. Ties break on node_id for reproducibility.
"""

from __future__ import annotations

import logging
import time
import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

logger = logging.getLogger("agentos.society.actor_scheduler")


class NodeClass(Enum):
    """Conceptual execution-location classes (Section 22) — reuses the
    vocabulary edge_device_coordinator.py's DeviceType already explored
    (CENTRAL/EDGE/MOBILE), renamed to this task's exact requested terms."""

    CLOUD = "cloud"
    EDGE = "edge"
    DEVICE = "device"
    ROBOT = "robot"
    """Deployment Migration audit: distinct from DEVICE so an operator's
    ACTOR_NODE_CLASS=robot is taken literally rather than silently
    falling back to CLOUD (actor_runtime.py's NodeClass(...) conversion
    only falls back for a value this enum truly doesn't define). No
    Scheduler logic currently branches on ROBOT vs. DEVICE differently —
    both are today just labels a placement's required_node_class/
    preferred_node_class can match against — that distinction can be
    added later without another enum change."""


class NodeHealth(Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    """Never heard from, or heartbeat stale beyond the health threshold —
    distinct from UNHEALTHY (a node that explicitly reported trouble):
    this is "we can't currently vouch for it," the scheduler's own
    equivalent of the Lifecycle Controller's actor staleness check."""


@dataclass(frozen=True)
class ExecutionNode:
    """One execution location the Scheduler can place Actors on. A node
    represents compute/execution location — it never owns Actor identity
    (Section 5)."""

    node_id: str
    node_class: NodeClass = NodeClass.CLOUD
    capacity: int = 1000
    """Max Actors this node can host."""
    current_actor_count: int = 0
    capabilities: tuple[str, ...] = ()
    """Free-text capability tags a placement can require, e.g. "gpu",
    "low_latency" — any string is valid, matching this codebase's own
    "free-text category, no code changes for new values" convention
    (see Society.society_type)."""
    region: str = ""
    reported_health: NodeHealth = NodeHealth.HEALTHY
    """What the node itself last reported — the Scheduler additionally
    treats a stale heartbeat as UNKNOWN regardless of this value (see
    PlanetaryRuntime.list_nodes' staleness computation), the same
    "observed can be more current than persisted" pattern the Lifecycle
    Controller already uses for actor staleness."""
    updated_at: float = field(default_factory=time.time)

    @property
    def available_capacity(self) -> int:
        return max(0, self.capacity - self.current_actor_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_class": self.node_class.value,
            "capacity": self.capacity,
            "current_actor_count": self.current_actor_count,
            "capabilities": list(self.capabilities),
            "region": self.region,
            "reported_health": self.reported_health.value,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ExecutionNode":
        try:
            node_class = NodeClass(d.get("node_class", "cloud"))
        except ValueError:
            node_class = NodeClass.CLOUD
        try:
            health = NodeHealth(d.get("reported_health", "healthy"))
        except ValueError:
            health = NodeHealth.UNKNOWN
        return ExecutionNode(
            node_id=d.get("node_id", ""),
            node_class=node_class,
            capacity=int(d.get("capacity", 1000)),
            current_actor_count=int(d.get("current_actor_count", 0)),
            capabilities=tuple(d.get("capabilities", [])),
            region=d.get("region", ""),
            reported_health=health,
            updated_at=float(d.get("updated_at", 0.0)),
        )


@dataclass(frozen=True)
class ActorPlacementRequirements:
    """Declarative placement constraints for one Actor — the
    ActorSpecification extension Section 7 asks for. Hard constraints
    (Section 8) eliminate a node outright; preferences only influence
    ranking among nodes that already satisfy every hard constraint. The
    default (no constraints at all) matches every healthy node with
    available capacity — today's implicit "any node will do" behavior,
    unchanged for any Actor that never specifies requirements."""

    required_capabilities: tuple[str, ...] = ()
    """HARD: the node must have ALL of these capability tags."""
    required_node_class: NodeClass | None = None
    """HARD: e.g. must be EDGE."""
    prohibited_node_ids: tuple[str, ...] = ()
    """HARD."""
    min_available_capacity: int = 1
    """HARD: the node must have room for this Actor."""
    preferred_node_class: NodeClass | None = None
    """SOFT."""
    preferred_region: str = ""
    """SOFT."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_capabilities": list(self.required_capabilities),
            "required_node_class": (self.required_node_class.value if self.required_node_class else None),
            "prohibited_node_ids": list(self.prohibited_node_ids),
            "min_available_capacity": self.min_available_capacity,
            "preferred_node_class": (self.preferred_node_class.value if self.preferred_node_class else None),
            "preferred_region": self.preferred_region,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ActorPlacementRequirements":
        def _class(v: str | None) -> NodeClass | None:
            if not v:
                return None
            try:
                return NodeClass(v)
            except ValueError:
                return None

        return ActorPlacementRequirements(
            required_capabilities=tuple(d.get("required_capabilities", [])),
            required_node_class=_class(d.get("required_node_class")),
            prohibited_node_ids=tuple(d.get("prohibited_node_ids", [])),
            min_available_capacity=int(d.get("min_available_capacity", 1)),
            preferred_node_class=_class(d.get("preferred_node_class")),
            preferred_region=d.get("preferred_region", ""),
        )


@dataclass(frozen=True)
class SchedulingDecision:
    """Every placement decision the Scheduler makes — explainable by
    construction (Section 10): candidates_rejected names every node ruled
    out and why; reason explains the winner, or explains why nothing
    qualified (Section 11 — UNSCHEDULABLE is a valid, explicit state, not
    a silent failure or a fabricated placement)."""

    actor_id: str
    scheduled: bool
    node_id: str = ""
    reason: str = ""
    candidates_considered: int = 0
    candidates_rejected: tuple[tuple[str, str], ...] = ()
    """(node_id, rejection_reason) for every node that did NOT qualify."""
    timestamp: float = field(default_factory=time.time)


_CAPACITY_HEADROOM_WEIGHT = 0.01
"""Tiny score contribution from available capacity — a genuine tiebreak
signal (prefer nodes with more headroom) that can never outweigh a real
preference match (preferred_node_class/region each score far higher),
keeping ranking deterministic and explainable rather than dominated by a
noisy load-balancing heuristic."""


class ActorScheduler:
    """Facilitates Actor placement for PlanetaryRuntime — owns none of
    the node registry or actor registry itself (same composition pattern
    as ActorLifecycleController/TransactionCoordinator: constructed with
    a back-reference to the owning PlanetaryRuntime, since resolving
    nodes and recording placement decisions both live there already)."""

    def __init__(self, planetary: "PlanetaryRuntime") -> None:
        self._planetary = planetary

    def schedule(
        self,
        actor_id: str,
        requirements: ActorPlacementRequirements | None = None,
        *,
        force: bool = False,
    ) -> SchedulingDecision:
        """Produce (or confirm) an Actor -> Execution Node assignment.

        Idempotent (Section 19): if the Actor already has a valid
        placement — a desired_node that still exists, is healthy, and
        still satisfies every hard constraint — that SAME node is
        returned rather than a fresh ranking being computed, so repeated
        scheduling never causes an unnecessary Actor restart. `force=True`
        (used by migrate_actor) bypasses this shortcut to compute a fresh
        decision even when the current placement is still nominally valid
        — e.g. a rebalance where a node's own preference changed.
        """
        requirements = requirements or ActorPlacementRequirements()

        if not self._planetary.list_nodes():
            # Unmanaged mode: no node has ever been registered anywhere in
            # the system, so this deployment has never opted into the node
            # registry / scheduler at all -- leave placement unconstrained
            # rather than declare every actor unschedulable, preserving
            # today's implicit single-process behavior unchanged for any
            # environment (including every pre-existing test in this repo)
            # that never calls register_self_as_node/register_node.
            return SchedulingDecision(
                actor_id=actor_id,
                scheduled=True,
                node_id="",
                reason="no nodes registered -- unmanaged single-node mode, placement unconstrained",
            )

        if not force:
            current_node_id = self._planetary.get_actor_desired_node(actor_id)
            if current_node_id:
                current_node = self._planetary.get_node(current_node_id)
                if current_node is not None and current_node.reported_health == NodeHealth.HEALTHY:
                    # Edge Deployment Validation finding (P1, confirmed
                    # live): a genuine one-actor-per-node registration
                    # (capacity=1 -- the documented Kubernetes/edge
                    # convention, see actor-deployment.yaml's own
                    # ACTOR_NODE_CAPACITY=1) always has current_actor_
                    # count=1 once THIS actor itself has registered —
                    # meaning available_capacity is permanently 0 on its
                    # OWN node, so this "am I already validly placed
                    # HERE" check was self-defeating: it always failed
                    # the capacity constraint against itself, forcing
                    # every reconcile to fall through to fresh candidate
                    # search, which then picked a DIFFERENT node with
                    # more headroom (e.g. the control plane's own,
                    # generic-capacity node) instead of confirming the
                    # actor's own explicit self-claim. Exclude this
                    # actor's own prior occupancy before checking --
                    # "can I stay here" must not double-count the very
                    # actor asking the question. Only affects this
                    # idempotent-shortcut check; fresh candidate search
                    # below (a real "can I move a NEW actor here")
                    # correctly still counts every currently-resident
                    # actor, unchanged.
                    self_excluded = dataclasses.replace(
                        current_node,
                        current_actor_count=max(0, current_node.current_actor_count - 1),
                    )
                    ok, _ = self._check_hard_constraints(self_excluded, requirements)
                    if ok:
                        return SchedulingDecision(
                            actor_id=actor_id,
                            scheduled=True,
                            node_id=current_node_id,
                            reason=f"already validly placed on {current_node_id}",
                        )

        nodes = self._planetary.list_nodes()
        healthy = [n for n in nodes if n.reported_health == NodeHealth.HEALTHY]
        rejected: list[tuple[str, str]] = [
            (n.node_id, "node is not healthy") for n in nodes if n.reported_health != NodeHealth.HEALTHY
        ]
        candidates: list[ExecutionNode] = []
        for n in healthy:
            ok, why = self._check_hard_constraints(n, requirements)
            if ok:
                candidates.append(n)
            else:
                rejected.append((n.node_id, why))

        if not candidates:
            reason = "no healthy nodes registered" if not healthy else self._summarize_no_candidate_reason(requirements)
            decision = SchedulingDecision(
                actor_id=actor_id,
                scheduled=False,
                reason=reason,
                candidates_considered=len(nodes),
                candidates_rejected=tuple(rejected),
            )
            self._publish_decision(decision)
            return decision

        ranked = self._rank_by_preferences(candidates, requirements)
        previous_node_id = self._planetary.get_actor_desired_node(actor_id) if force else ""

        # Try ranked candidates in order, reserving capacity atomically
        # (Section 17-18) -- a concurrent scheduling decision for a
        # different actor can win the reservation race on the top choice
        # between ranking and reserving; falling to the next candidate
        # keeps this actor schedulable rather than failing outright on a
        # transient race.
        for selected in ranked:
            if self._planetary._reserve_node_capacity(selected.node_id, 1) is None:
                rejected.append(
                    (
                        selected.node_id,
                        "lost capacity reservation race (concurrent placement)",
                    )
                )
                continue
            self._planetary.set_actor_desired_node(actor_id, selected.node_id)
            if previous_node_id and previous_node_id != selected.node_id:
                self._planetary._reserve_node_capacity(previous_node_id, -1)
            decision = SchedulingDecision(
                actor_id=actor_id,
                scheduled=True,
                node_id=selected.node_id,
                reason=self._explain_selection(selected, requirements),
                candidates_considered=len(nodes),
                candidates_rejected=tuple(rejected),
            )
            self._publish_decision(decision)
            return decision

        decision = SchedulingDecision(
            actor_id=actor_id,
            scheduled=False,
            reason="every otherwise-qualifying node lost its capacity reservation race",
            candidates_considered=len(nodes),
            candidates_rejected=tuple(rejected),
        )
        self._publish_decision(decision)
        return decision

    def migrate_actor(self, actor_id: str, target_node_id: str | None = None) -> SchedulingDecision:
        """Deliberate rescheduling for an Actor already RUNNING somewhere
        (Section 14). Safe checkpoint-and-restart, never unsafe live
        migration: this only updates the desired placement record and, if
        the Actor is resident on THIS process, checkpoints and suspends
        it here (reusing ActorLifecycleController.reconcile()'s own
        suspend path) — it does NOT reach into a different process to
        stop anything. Whichever node's reconciliation loop next observes
        (desired=RUNNING, resident here, desired_node != this node) picks
        the Actor back up via the ordinary _do_start/_do_resume path,
        restoring from the same checkpoint every other recovery uses —
        Actor identity and persistent state are untouched throughout."""
        requirements_raw = self._planetary.get_actor_placement_requirements(actor_id)
        if target_node_id is not None:
            node = self._planetary.get_node(target_node_id)
            if node is None:
                decision = SchedulingDecision(
                    actor_id=actor_id,
                    scheduled=False,
                    reason=f"target node {target_node_id!r} is not registered",
                )
                self._publish_decision(decision)
                return decision
            self._planetary.set_actor_desired_node(actor_id, target_node_id)
            decision = SchedulingDecision(
                actor_id=actor_id,
                scheduled=True,
                node_id=target_node_id,
                reason=f"explicit migration target {target_node_id}",
            )
        else:
            decision = self.schedule(actor_id, requirements_raw, force=True)

        if decision.scheduled:
            self._planetary.suspend_actor_for_migration(actor_id)
            self._publish_migration_event(actor_id, decision.node_id)
        return decision

    def _check_hard_constraints(self, node: ExecutionNode, req: ActorPlacementRequirements) -> tuple[bool, str]:
        if req.required_node_class is not None and node.node_class != req.required_node_class:
            return (
                False,
                f"node_class {node.node_class.value!r} != required {req.required_node_class.value!r}",
            )
        if node.node_id in req.prohibited_node_ids:
            return False, "node is prohibited for this actor"
        missing = set(req.required_capabilities) - set(node.capabilities)
        if missing:
            return False, f"missing required capabilities: {sorted(missing)}"
        if node.available_capacity < req.min_available_capacity:
            return (
                False,
                f"insufficient available capacity ({node.available_capacity} < {req.min_available_capacity})",
            )
        return True, ""

    def _rank_by_preferences(
        self, candidates: list[ExecutionNode], req: ActorPlacementRequirements
    ) -> list[ExecutionNode]:
        def score(node: ExecutionNode) -> float:
            s = 0.0
            if req.preferred_node_class is not None and node.node_class == req.preferred_node_class:
                s += 10.0
            if req.preferred_region and node.region == req.preferred_region:
                s += 5.0
            s += node.available_capacity * _CAPACITY_HEADROOM_WEIGHT
            return s

        # node_id as the final tiebreak -- identical inputs always produce
        # an identical ranking (Section 9: reproducible), never dependent
        # on dict/set iteration order.
        return sorted(candidates, key=lambda n: (-score(n), n.node_id))

    def _explain_selection(self, node: ExecutionNode, req: ActorPlacementRequirements) -> str:
        checks = [
            "node healthy",
            f"available capacity {node.available_capacity} >= {req.min_available_capacity}",
        ]
        if req.required_capabilities:
            checks.append(f"required capabilities satisfied: {list(req.required_capabilities)}")
        if req.required_node_class is not None:
            checks.append(f"node_class {node.node_class.value} matches required")
        if req.preferred_node_class is not None:
            checks.append(
                f"preferred node_class {'satisfied' if node.node_class == req.preferred_node_class else 'not satisfied'}"
            )
        if req.preferred_region:
            checks.append(f"preferred region {'satisfied' if node.region == req.preferred_region else 'not satisfied'}")
        return f"scheduled to {node.node_id}: " + "; ".join(checks)

    def _summarize_no_candidate_reason(self, req: ActorPlacementRequirements) -> str:
        parts = []
        if req.required_capabilities:
            parts.append(f"required capabilities {list(req.required_capabilities)}")
        if req.required_node_class is not None:
            parts.append(f"required node_class {req.required_node_class.value}")
        if req.min_available_capacity > 1:
            parts.append(f"min_available_capacity {req.min_available_capacity}")
        if not parts:
            return "no healthy node has available capacity"
        return f"no healthy node satisfies: {'; '.join(parts)}"

    def _publish_decision(self, decision: SchedulingDecision) -> None:
        try:
            from src.monkey_brain.kernel.society.context_stream import (
                ContextEvent,
                ContextEventType,
            )

            description = (
                f"{decision.actor_id}: {decision.reason}"
                if decision.scheduled
                else f"{decision.actor_id}: UNSCHEDULABLE — {decision.reason}"
            )
            self._planetary.context_stream.publish(
                ContextEvent(
                    event_type=ContextEventType.ACTOR_LIFECYCLE,
                    actor_id=decision.actor_id,
                    description=description,
                    payload={
                        "event_type": ("actor_scheduled" if decision.scheduled else "actor_unschedulable"),
                        "node_id": decision.node_id,
                        "reason": decision.reason,
                        "candidates_considered": decision.candidates_considered,
                        "candidates_rejected": list(decision.candidates_rejected),
                    },
                    provenance="actor_scheduler",
                )
            )
        except Exception:
            logger.debug("_publish_decision: publish failed (non-fatal)", exc_info=True)
        try:
            from src.monkey_brain.kernel.pipeline.audit_trail import (
                record_decision_event,
            )

            record_decision_event(
                "actor_scheduled" if decision.scheduled else "actor_unschedulable",
                actor_id=decision.actor_id,
                reason=decision.reason,
                metadata={
                    "node_id": decision.node_id,
                    "candidates_considered": decision.candidates_considered,
                    "candidates_rejected": list(decision.candidates_rejected),
                    "source": "ActorScheduler",
                },
            )
        except Exception:
            logger.debug(
                "_publish_decision: audit_trail record failed (non-fatal)",
                exc_info=True,
            )

    def _publish_migration_event(self, actor_id: str, node_id: str) -> None:
        try:
            from src.monkey_brain.kernel.society.context_stream import (
                ContextEvent,
                ContextEventType,
            )

            self._planetary.context_stream.publish(
                ContextEvent(
                    event_type=ContextEventType.ACTOR_LIFECYCLE,
                    actor_id=actor_id,
                    description=f"{actor_id}: migrating to {node_id}",
                    payload={"event_type": "actor_migrating", "node_id": node_id},
                    provenance="actor_scheduler",
                )
            )
        except Exception:
            logger.debug("_publish_migration_event: publish failed (non-fatal)", exc_info=True)
