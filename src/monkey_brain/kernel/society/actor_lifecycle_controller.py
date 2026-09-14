"""Actor Lifecycle Controller — the CognitiveOS Control Plane component
that manages an Actor's LIFECYCLE, never its cognition.

    Desired Actor State
           ↓
    Actor Lifecycle Controller
           ↓
    Observed Actor State
           ↓
    Reconciliation
           ↓
    Actor Runtime

This is the Kubernetes-controller analog for the CognitiveOS deployment
abstraction established in DEPLOYMENT_ARCHITECTURE.md: an Actor is the
independently-deployable, autonomous cognitive unit (the Pod analog); this
controller answers "what Actors should exist and in what state" versus
"what Actors actually exist and what state are they in," and reconciles
the difference — exactly what a Kubernetes controller does for Pods,
never what the Pod's own application code does.

THE MOST IMPORTANT INVARIANT: the controller manages the Actor. The Actor
manages its own cognition. Nothing here ever plans, decides, executes a
capability, or forms a belief on an actor's behalf — every action below
either (a) flips is_active/status on an already-constructed ActorRuntimeState,
(b) calls an already-existing, already-governed method (register_actor,
checkpoint_actor_belief, restore_actor_belief, unregister_actor), or (c)
publishes an event describing what happened. It never reaches into
capability dispatch, TransitionGate, or delegation — starting an actor
does not grant it any authority it didn't already have (Deployment
Architecture, Section 14): authority is established at capability-call
time (action_executor.py -> TransitionGate/domain_security.py), not by
this controller marking an actor RUNNING.

Deliberately NOT built here (Section 23 — do not overengineer): a generic
process manager, a second event system, a second persistence layer, a new
locking primitive (reuses PlanetaryRuntime.acquire_actor_lease, already
built for exactly this "don't let two nodes touch one actor concurrently"
problem), or a full replay/resume-arbitrary-business-action mechanism —
crash recovery restarts the Actor's cognition from its last checkpoint;
it does not, and must not, re-invoke a specific capability call.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.society.actor_lifecycle import (
    ActorDesiredState,
    LifecycleEvent,
    LifecycleEventType,
    ObservedActorState,
    ReconciliationResult,
)
from src.monkey_brain.kernel.society.domain import ActorStatus

if TYPE_CHECKING:
    from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

logger = logging.getLogger("agentos.society.actor_lifecycle_controller")

# Actions dispatched by _decide() — the vocabulary ReconciliationResult.action uses.
_ACTION_NONE = "none"
_ACTION_START = "start"
_ACTION_RESUME = "resume"
_ACTION_SUSPEND = "suspend"
_ACTION_TERMINATE = "terminate"
_ACTION_RECOVER = "recover"
_ACTION_SKIPPED_UNKNOWN = "skipped_unknown_actor"
_ACTION_SKIPPED_LEASE = "skipped_lease_held"
_ACTION_UNSCHEDULABLE = "unschedulable"
_ACTION_SCHEDULED_ELSEWHERE = "scheduled_elsewhere"
_ACTION_MIGRATE_AWAY = "migrate_away"
"""An already-RUNNING, locally-resident actor whose scheduler placement
now points at a DIFFERENT node (Actor Scheduler spec, Section 14) —
suspend it here so the target node's own reconcile loop can pick it up;
never a termination, never a desired-state change."""

_RUNNING_LIKE = frozenset({ActorStatus.ACTIVE.value, ActorStatus.INITIALIZED.value})
_DORMANT_LIKE = frozenset({ActorStatus.REGISTERED.value, ActorStatus.IDLE.value, ""})


class ActorLifecycleController:
    """Facilitates actor lifecycle for PlanetaryRuntime — owns none of the
    actor's cognition or persistence itself (same composition pattern as
    TransactionCoordinator/GameTheoryRuntime: constructed with a back-
    reference to the owning PlanetaryRuntime, since resolving actors,
    registry state, leases, and persistence all live there already)."""

    def __init__(self, planetary: "PlanetaryRuntime") -> None:
        self._planetary = planetary

    # ── Public desired-state API ────────────────────────────────────────

    def set_desired_state(self, actor_id: str, desired: ActorDesiredState, *, reason: str = "") -> None:
        self._planetary.set_actor_desired_state(actor_id, desired, reason=reason)

    def get_desired_state(self, actor_id: str) -> ActorDesiredState:
        return self._planetary.get_actor_desired_state(actor_id)

    def observe(self, actor_id: str) -> ObservedActorState:
        return self._planetary.observe_actor(actor_id)

    # ── Reconciliation ───────────────────────────────────────────────────

    def reconcile(self, actor_id: str) -> ReconciliationResult:
        """Bring one actor's observed state toward its desired state.
        Idempotent (Section 7): reconciling an already-settled actor is a
        pure read, no writes, no lease taken. Safe under concurrent
        invocation (Section 13): a real action is only attempted after
        this actor's ownership lease is held, and the desired/observed
        comparison is re-checked under the lease before acting, so two
        overlapping reconcile() calls for the same actor_id never both
        decide to act -- the second one either finds nothing left to do
        (the first already did it) or finds the lease unavailable
        (the first is still doing it) and reports skipped_lease_held."""
        desired = self._planetary.get_actor_desired_state(actor_id)
        observed = self._planetary.observe_actor(actor_id)
        action = self._decide(desired, observed)
        if action in (_ACTION_NONE, _ACTION_SKIPPED_UNKNOWN):
            return ReconciliationResult(
                actor_id=actor_id,
                desired_state=desired.value,
                observed_before=observed.status,
                action=action,
                succeeded=(action == _ACTION_NONE),
                reason=(
                    ""
                    if action == _ACTION_NONE
                    else "actor was never registered — the controller does not create new actors"
                ),
            )

        token = self._planetary.acquire_actor_lease(actor_id)
        if token is None:
            return ReconciliationResult(
                actor_id=actor_id,
                desired_state=desired.value,
                observed_before=observed.status,
                action=_ACTION_SKIPPED_LEASE,
                succeeded=True,
                reason="another node or an in-flight tick currently owns this actor's lease",
            )
        try:
            # Re-observe under the lease: another reconcile pass (or the
            # actor's own tick) may have changed things between the cheap
            # read above and now.
            observed = self._planetary.observe_actor(actor_id, reconcile_lease_token=token)
            action = self._decide(desired, observed)
            if action in (_ACTION_NONE, _ACTION_SKIPPED_UNKNOWN):
                return ReconciliationResult(
                    actor_id=actor_id,
                    desired_state=desired.value,
                    observed_before=observed.status,
                    action=action,
                    succeeded=(action == _ACTION_NONE),
                )
            dispatch = {
                _ACTION_START: self._do_start,
                _ACTION_RESUME: self._do_resume,
                _ACTION_SUSPEND: self._do_suspend,
                _ACTION_TERMINATE: self._do_terminate,
                _ACTION_RECOVER: self._do_recover,
                _ACTION_MIGRATE_AWAY: self._do_migrate_away,
            }[action]
            return dispatch(actor_id, desired, observed)
        finally:
            self._planetary.release_actor_lease(actor_id, token)

    def reconcile_all(self) -> list[ReconciliationResult]:
        """Reconcile every actor the registry currently knows about — the
        background loop's sweep (PlanetaryRuntime._actor_lifecycle_
        reconciliation_loop). Does not discover actor_ids that only have
        a desired-state record but were never registered (see module
        docstring / docs/ACTOR_LIFECYCLE.md's Known Limitations) — a
        direct reconcile(actor_id) call still reports skipped_unknown_actor
        correctly for that case."""
        results: list[ReconciliationResult] = []
        for entry in self._planetary.list_registry():
            try:
                results.append(self.reconcile(entry.actor_id))
            except Exception as exc:
                logger.error(
                    "reconcile_all: reconcile(%r) raised: %s",
                    entry.actor_id,
                    exc,
                    exc_info=True,
                )
                results.append(
                    ReconciliationResult(
                        actor_id=entry.actor_id,
                        desired_state="",
                        observed_before="",
                        action="error",
                        succeeded=False,
                        reason=str(exc),
                    )
                )
        return results

    def reconcile_rehydrated_actors(self) -> list[ReconciliationResult]:
        """Enforce desired state for rehydrated actors at boot time.

        After actor state rehydration from MongoDB, this method immediately
        reconciles all actors to enforce their persisted desired state,
        ensuring actors don't unexpectedly become active if they were
        previously PAUSED/SUSPENDED/TERMINATED.

        This is called automatically from PlanetaryRuntime._init_persistence()
        after rehydration completes. Non-blocking: exceptions don't crash boot.

        Returns:
            List of ReconciliationResult for each rehydrated actor
        """
        results: list[ReconciliationResult] = []
        try:
            logger.info("Enforcing desired state for rehydrated actors")
            for entry in self._planetary.list_registry():
                try:
                    result = self.reconcile(entry.actor_id)
                    results.append(result)
                    if result.succeeded and result.action != _ACTION_NONE:
                        logger.info(
                            "Rehydrated actor %s: applied action %s (desired: %s)",
                            entry.actor_id,
                            result.action,
                            result.desired_state,
                        )
                except Exception as exc:
                    logger.warning(
                        "reconcile_rehydrated_actors: reconcile(%r) raised: %s",
                        entry.actor_id,
                        exc,
                    )
                    results.append(
                        ReconciliationResult(
                            actor_id=entry.actor_id,
                            desired_state="",
                            observed_before="",
                            action="error",
                            succeeded=False,
                            reason=str(exc),
                        )
                    )
        except Exception as exc:
            logger.error("reconcile_rehydrated_actors failed: %s", exc)

        return results

    def _decide(self, desired: ActorDesiredState, observed: ObservedActorState) -> str:
        if desired == ActorDesiredState.TERMINATED:
            return _ACTION_TERMINATE if observed.exists else _ACTION_NONE

        if not observed.exists:
            return _ACTION_SKIPPED_UNKNOWN

        if desired == ActorDesiredState.SUSPENDED:
            return _ACTION_NONE if observed.status == ActorStatus.SUSPENDED.value else _ACTION_SUSPEND

        # desired == RUNNING
        if observed.status == ActorStatus.FAILED.value or observed.is_stale:
            return _ACTION_RECOVER
        if observed.status == ActorStatus.SUSPENDED.value:
            return _ACTION_RESUME
        if observed.status in _RUNNING_LIKE:
            # Migration detection (Section 14): the actor is already
            # running HERE, but the scheduler's current placement points
            # somewhere else. No restart action would otherwise ever be
            # triggered for an already-healthy actor, so this is the one
            # place _decide() itself, rather than _do_start/_do_resume,
            # must consult placement -- everywhere else, START/RESUME
            # naturally consult the scheduler themselves before acting.
            if (
                observed.resident_here
                and observed.desired_node_id
                and observed.desired_node_id != self._planetary._node_id
            ):
                return _ACTION_MIGRATE_AWAY
            # Fast-restart detection (Actor Artifact model, Section 11:
            # "process killed -> same Actor binary starts -> Actor
            # restored" must not depend on waiting out
            # _ACTOR_STALE_SECONDS, default 600s). observed.is_stale only
            # catches this once the timeout elapses; a process that
            # restarted with the SAME node identity seconds ago is not
            # resident_here (its own in-memory _actors dict is empty
            # again) yet the registry's last-known node_id still names
            # THIS process specifically -- an unambiguous "I am supposed
            # to have this, but I don't" signal that doesn't require
            # waiting on staleness at all, since there is no cross-process
            # ambiguity to protect against here: the record's own owner
            # is asking. A different process's own not-yet-stale record
            # (observed.node_id naming some OTHER node) is deliberately
            # left alone here -- that remains exactly the staleness-gated
            # crash detection above, unchanged.
            if not observed.resident_here and observed.node_id == self._planetary._node_id:
                return _ACTION_RECOVER
            return _ACTION_NONE
        # REGISTERED / IDLE / unknown: known to exist, not actively ticking.
        return _ACTION_START

    # ── Actions (each idempotent on its own — reconcile()'s lease is what
    #    makes them safe under concurrency; these methods trust the caller
    #    already holds it) ────────────────────────────────────────────────

    def _do_start(
        self,
        actor_id: str,
        desired: ActorDesiredState,
        observed: ObservedActorState,
        *,
        reason: str = "",
    ) -> ReconciliationResult:
        placement = self._consult_scheduler(actor_id, desired, observed)
        if placement is not None:
            return placement
        self._publish(
            actor_id,
            LifecycleEventType.ACTOR_STARTING,
            observed.status,
            "starting",
            reason,
        )
        # Ensure the actor is actually loaded before touching it — reuse
        # the existing reconstruction path (profile + belief + affiliations
        # from the Redis actor registry), never a parallel one.
        if self._planetary.get_actor_runtime(actor_id) is None:
            self._planetary.reconcile_actors_from_redis()
        sr = self._planetary._home_society_runtime(actor_id)
        state = sr.get_actor(actor_id) if sr is not None else None
        if state is None:
            self._publish(
                actor_id,
                LifecycleEventType.ACTOR_FAILED,
                "starting",
                ActorStatus.FAILED.value,
                "actor could not be reconstructed from the registry",
            )
            return ReconciliationResult(
                actor_id=actor_id,
                desired_state=desired.value,
                observed_before=observed.status,
                action=_ACTION_START,
                succeeded=False,
                reason="reconstruction failed — actor not found in registry",
            )
        # Distinguish "process started" from "Actor ready" from "Actor
        # operational" (Section 9): restore its last committed belief
        # BEFORE marking it eligible to tick, so the very first tick after
        # a start/recover already sees real state, never an empty one.
        self._planetary.restore_actor_belief(actor_id)
        self._publish(actor_id, LifecycleEventType.ACTOR_READY, "starting", "ready", reason)
        sr.activate_actor(actor_id)
        self._refresh_registry(actor_id)
        self._publish(
            actor_id,
            LifecycleEventType.ACTOR_STARTED,
            "ready",
            ActorStatus.ACTIVE.value,
            reason,
        )
        return ReconciliationResult(
            actor_id=actor_id,
            desired_state=desired.value,
            observed_before=observed.status,
            action=_ACTION_START,
            succeeded=True,
            reason=reason,
        )

    def _do_resume(
        self,
        actor_id: str,
        desired: ActorDesiredState,
        observed: ObservedActorState,
        *,
        reason: str = "",
    ) -> ReconciliationResult:
        placement = self._consult_scheduler(actor_id, desired, observed)
        if placement is not None:
            return placement
        self._publish(
            actor_id,
            LifecycleEventType.ACTOR_RESUMING,
            observed.status,
            "resuming",
            reason,
        )
        if self._planetary.get_actor_runtime(actor_id) is None:
            self._planetary.reconcile_actors_from_redis()
        # SUSPENDED->RUNNING restores from the same checkpoint mechanism
        # every other belief restoration uses — never bypassed or
        # special-cased for the lifecycle path (Section 11).
        self._planetary.restore_actor_belief(actor_id)
        sr = self._planetary._home_society_runtime(actor_id)
        if sr is None or sr.get_actor(actor_id) is None:
            self._publish(
                actor_id,
                LifecycleEventType.ACTOR_FAILED,
                "resuming",
                ActorStatus.FAILED.value,
                "actor not found while resuming",
            )
            return ReconciliationResult(
                actor_id=actor_id,
                desired_state=desired.value,
                observed_before=observed.status,
                action=_ACTION_RESUME,
                succeeded=False,
                reason="actor not found while resuming",
            )
        sr.activate_actor(actor_id)
        self._refresh_registry(actor_id)
        self._publish(
            actor_id,
            LifecycleEventType.ACTOR_RESUMED,
            "resuming",
            ActorStatus.ACTIVE.value,
            reason,
        )
        return ReconciliationResult(
            actor_id=actor_id,
            desired_state=desired.value,
            observed_before=observed.status,
            action=_ACTION_RESUME,
            succeeded=True,
            reason=reason,
        )

    def _do_suspend(
        self,
        actor_id: str,
        desired: ActorDesiredState,
        observed: ObservedActorState,
        *,
        reason: str = "",
    ) -> ReconciliationResult:
        self._publish(
            actor_id,
            LifecycleEventType.ACTOR_SUSPENDING,
            observed.status,
            "suspending",
            reason,
        )
        # Checkpoint BEFORE marking suspended — RUNNING -> checkpoint/
        # persist -> SUSPENDED (Section 11), never the reverse order.
        self._planetary.checkpoint_actor_belief(actor_id)
        sr = self._planetary._home_society_runtime(actor_id)
        state = sr.get_actor(actor_id) if sr is not None else None
        if state is None:
            return ReconciliationResult(
                actor_id=actor_id,
                desired_state=desired.value,
                observed_before=observed.status,
                action=_ACTION_SUSPEND,
                succeeded=False,
                reason="actor not resident in this process",
            )
        state.is_active = False
        state.status = ActorStatus.SUSPENDED
        # checkpoint_actor_belief()'s own registry refresh ran BEFORE this
        # status flip, so it captured the pre-suspend status — refresh
        # again now so a cross-process locate_actor()/list_registry() read
        # sees SUSPENDED, not stale ACTIVE.
        self._refresh_registry(actor_id)
        self._publish(
            actor_id,
            LifecycleEventType.ACTOR_SUSPENDED,
            "suspending",
            ActorStatus.SUSPENDED.value,
            reason,
        )
        return ReconciliationResult(
            actor_id=actor_id,
            desired_state=desired.value,
            observed_before=observed.status,
            action=_ACTION_SUSPEND,
            succeeded=True,
            reason=reason,
        )

    def _do_terminate(
        self,
        actor_id: str,
        desired: ActorDesiredState,
        observed: ObservedActorState,
        *,
        reason: str = "",
    ) -> ReconciliationResult:
        self._publish(
            actor_id,
            LifecycleEventType.ACTOR_TERMINATING,
            observed.status,
            "terminating",
            reason,
        )
        self._warn_if_negotiation_pending(actor_id)
        # checkpoint-before-terminate now lives INSIDE unregister_actor()
        # itself (PlanetaryRuntime), not duplicated here — every caller
        # (this controller, DELETE /actors/{id}, any future one) gets the
        # same safety guarantee from one place, rather than each call site
        # needing to remember to checkpoint first.
        ok = self._planetary.unregister_actor(actor_id)
        # Release this actor's scheduler capacity reservation, if any --
        # a terminated actor no longer occupies a placement slot. Best-
        # effort: a missed release here is corrected by the target node's
        # next heartbeat recount (heartbeat_node), never silently lost
        # forever.
        placed_node = self._planetary.get_actor_desired_node(actor_id)
        if placed_node:
            self._planetary._reserve_node_capacity(placed_node, -1)
        self._publish(
            actor_id,
            LifecycleEventType.ACTOR_TERMINATED,
            "terminating",
            ActorStatus.TERMINATED.value,
            reason,
        )
        return ReconciliationResult(
            actor_id=actor_id,
            desired_state=desired.value,
            observed_before=observed.status,
            action=_ACTION_TERMINATE,
            succeeded=ok,
            reason=reason,
        )

    def _do_recover(
        self,
        actor_id: str,
        desired: ActorDesiredState,
        observed: ObservedActorState,
        *,
        reason: str = "",
    ) -> ReconciliationResult:
        """Crash recovery (Section 12): the controller restarts the
        ACTOR — it never replays a specific business action. Reuses
        _do_start's exact reconstruct-and-restore path; the actor's next
        real tick begins a fresh planning cycle from its last committed
        belief. Nothing here re-invokes a capability, resubmits a plan,
        or touches negotiation/execution-checkpoint state directly."""
        effective_reason = reason or (
            f"registry record stale since {observed.updated_at:.0f} with no lease held — treating as crashed"
        )
        self._publish(
            actor_id,
            LifecycleEventType.ACTOR_FAILED,
            observed.status,
            ActorStatus.FAILED.value,
            effective_reason,
        )
        self._publish(
            actor_id,
            LifecycleEventType.ACTOR_RECOVERING,
            ActorStatus.FAILED.value,
            "recovering",
            effective_reason,
        )
        result = self._do_start(actor_id, desired, observed, reason=effective_reason)
        if result.succeeded:
            self._publish(
                actor_id,
                LifecycleEventType.ACTOR_RECOVERED,
                "recovering",
                ActorStatus.ACTIVE.value,
                effective_reason,
            )
        return ReconciliationResult(
            actor_id=actor_id,
            desired_state=desired.value,
            observed_before=observed.status,
            action=_ACTION_RECOVER,
            succeeded=result.succeeded,
            reason=effective_reason,
        )

    def _consult_scheduler(
        self, actor_id: str, desired: ActorDesiredState, observed: ObservedActorState
    ) -> ReconciliationResult | None:
        """Ask the Scheduler where this actor belongs before actually
        starting/resuming it here (Actor Scheduler spec, Section 12:
        strict Scheduler <-> Lifecycle Controller separation — the
        Scheduler only ever proposes a placement via the node/actor
        registries, it never touches an actor process directly; THIS
        controller is the only thing that acts on that proposal). Returns
        None (proceed with the local start/resume as normal) when this
        node IS the scheduled node, or when the scheduler is running in
        unmanaged single-node mode (node_id=""). Returns a terminal
        ReconciliationResult otherwise — either UNSCHEDULABLE (Section
        11: an explicit, valid, non-fabricating state; the controller
        does not retry into a fake placement) or scheduled_elsewhere (a
        different node should start it; this node correctly does
        nothing)."""
        requirements = self._planetary.get_actor_placement_requirements(actor_id)
        decision = self._planetary.scheduler.schedule(actor_id, requirements)
        if not decision.scheduled:
            self._publish(
                actor_id,
                LifecycleEventType.ACTOR_UNSCHEDULABLE,
                observed.status,
                observed.status,
                decision.reason,
            )
            # Gap Remediation audit finding (Priority 1 — Scheduler ->
            # Kubernetes gap): the ONE UNSCHEDULABLE cause this can
            # actually fix is "no healthy node exists at all" -- every
            # other cause (a real node exists but rejects this actor's
            # specific requirements) needs an operator/capacity decision,
            # not a generic Pod. should_provision() draws that line.
            # Opt-in (KUBERNETES_PROVISIONING_ENABLED, default off) --
            # zero behavior change for any deployment that never enables
            # it or has no kubectl configured; a failed/skipped
            # provisioning attempt leaves UNSCHEDULABLE exactly as before.
            from src.monkey_brain.kernel.society import (
                kubernetes_provisioner as _k8s_provisioner,
            )

            if _k8s_provisioner.provisioning_enabled() and _k8s_provisioner.should_provision(decision.reason):
                # Actors default to edge: an actor with no explicit
                # required_node_class gets a dedicated EDGE Pod, not a
                # second cloud-class node — the control plane already IS
                # the one cloud-class node (SCHEDULER_NODE_CAPACITY=0,
                # deployment.yaml), so provisioning another "cloud" Pod
                # here would just recreate the same non-actor-hosting
                # node under a different name instead of giving this
                # actor the dedicated Pod it actually needs.
                provisioned = self._planetary.kubernetes_provisioner.provision(
                    actor_id,
                    node_class=(requirements.required_node_class.value if requirements.required_node_class else "edge"),
                )
                if provisioned:
                    # Wake the fast path instead of waiting for the 300s
                    # backstop to notice the new Pod once it registers
                    # itself as a node -- see the same reasoning the
                    # scheduled_elsewhere re-enqueue below documents.
                    self._planetary._enqueue_reconciliation(actor_id)
            return ReconciliationResult(
                actor_id=actor_id,
                desired_state=desired.value,
                observed_before=observed.status,
                action=_ACTION_UNSCHEDULABLE,
                succeeded=False,
                reason=decision.reason,
            )
        if decision.node_id and decision.node_id != self._planetary._node_id:
            reason = f"scheduled to {decision.node_id}, not this node ({self._planetary._node_id})"
            self._publish(
                actor_id,
                LifecycleEventType.ACTOR_SCHEDULED_ELSEWHERE,
                observed.status,
                observed.status,
                reason,
            )
            # Edge Deployment: the Kubernetes-only analog of this problem
            # doesn't exist there -- a Pod's own actor_runtime.py process
            # boots itself and self-claims via ACTOR_CLAIM_PLACEMENT, so
            # nothing external ever needs to "start" it. An edge device
            # has no such self-booting process for an actor that has
            # never run anywhere yet -- something has to ask its Edge
            # Agent to spawn one. Scoped narrowly: only when the actor
            # has genuinely never been resident anywhere (REGISTERED/
            # INITIALIZED/UNKNOWN, never ACTIVE/SUSPENDED/IDLE) and the
            # scheduled node is registered as EDGE/DEVICE/ROBOT-class --
            # an actor merely suspended-for-migration elsewhere is left
            # alone here; its own target node's normal reconcile (or,
            # for Kubernetes, its own Pod boot) handles that case
            # already, unchanged.
            from src.monkey_brain.kernel.society import (
                edge_provisioner as _edge_provisioner,
            )

            if _edge_provisioner.provisioning_enabled() and observed.status not in (
                ActorStatus.ACTIVE.value,
                ActorStatus.SUSPENDED.value,
                ActorStatus.IDLE.value,
            ):
                target_node = self._planetary.get_node(decision.node_id)
                if target_node is not None and target_node.node_class.value in (
                    "edge",
                    "device",
                    "robot",
                ):
                    provisioned = self._planetary.edge_provisioner.provision(
                        actor_id,
                        device_id=decision.node_id,
                        node_class=target_node.node_class.value,
                    )
                    if provisioned:
                        self._planetary._enqueue_reconciliation(actor_id)
            # Gap Remediation audit finding (Priority 1 — multi-process
            # reconcile-queue race, confirmed live in the prior
            # conformance-testing session): LPOP is exclusive, so
            # whichever process happens to drain this actor_id's queue
            # entry first "consumes" it -- if that process isn't the
            # scheduled node, the entry is gone and the ACTUAL target
            # had no fast-path signal, only the 300s backstop sweep.
            # Re-enqueueing here gives every OTHER process (including,
            # eventually, the correct one) another 2s-interval chance.
            # Bounded, not unbounded: only the processes that actually
            # attempt this actor_id (at most once per their own 2s
            # queue-drain cycle) ever re-enqueue it, and the correct
            # node's own successful resume/start naturally stops the
            # cycle (its next _decide() sees the settled state and
            # returns NONE, doing nothing further).
            self._planetary._enqueue_reconciliation(actor_id)
            return ReconciliationResult(
                actor_id=actor_id,
                desired_state=desired.value,
                observed_before=observed.status,
                action=_ACTION_SCHEDULED_ELSEWHERE,
                succeeded=True,
                reason=reason,
            )
        return None

    def _do_migrate_away(
        self,
        actor_id: str,
        desired: ActorDesiredState,
        observed: ObservedActorState,
        *,
        reason: str = "",
    ) -> ReconciliationResult:
        """Evacuate an actor from THIS node because the scheduler now
        places it elsewhere — checkpoint + local suspend only (Section
        14: safe checkpoint-and-restart, never unsafe live migration).
        desired_state is deliberately left untouched (still RUNNING):
        the intent never changed, only location. The target node's own
        reconcile loop resumes it from the same checkpoint via the
        ordinary SUSPENDED+RUNNING-desired -> _ACTION_RESUME path."""
        effective_reason = reason or f"scheduler placed this actor on {observed.desired_node_id}, evacuating this node"
        self._publish(
            actor_id,
            LifecycleEventType.ACTOR_SCHEDULED_ELSEWHERE,
            observed.status,
            "suspending_for_migration",
            effective_reason,
        )
        ok = self._planetary.suspend_actor_for_migration(actor_id)
        if ok:
            # Live Deployment Validation finding: confirmed live -- an
            # actor suspended here (e.g. by the control-plane's own
            # backstop sweep, which legitimately sees every actor since
            # it never sets ACTOR_ID) sat SUSPENDED for 8+ minutes past
            # its target node's 300s backstop interval before converging,
            # because nothing woke that target node's fast queue-drain
            # path -- only _consult_scheduler's scheduled_elsewhere/
            # UNSCHEDULABLE branches re-enqueued after a state change,
            # this sibling suspend path never did. A manual RPUSH onto
            # the reconcile queue for this exact actor_id resolved it
            # within seconds, proving the RESUME decision logic itself
            # was correct all along -- only the wake-up signal was
            # missing. Same bounded reasoning as the other re-enqueue
            # fixes this session: the target node's own next _decide()
            # sees the settled state and returns NONE, so this cannot
            # loop.
            self._planetary._enqueue_reconciliation(actor_id)
        return ReconciliationResult(
            actor_id=actor_id,
            desired_state=desired.value,
            observed_before=observed.status,
            action=_ACTION_MIGRATE_AWAY,
            succeeded=ok,
            reason=effective_reason,
        )

    def _refresh_registry(self, actor_id: str) -> None:
        """Write this actor's CURRENT in-memory status/node_id/updated_at
        back to the durable registry (PlanetaryRuntime._save_actor — the
        same O(1)-per-actor write checkpoint_actor_belief's own registry
        refresh uses) immediately after a status change this controller
        made. Without this, a cross-process locate_actor()/list_registry()
        read would see whatever status was last persisted at registration
        or the last real request checkpoint — stale by however long ago
        that was, not "as of the transition that just happened." Never
        raises; a registry-refresh failure must not undo the lifecycle
        transition that already succeeded."""
        try:
            sr = self._planetary._home_society_runtime(actor_id)
            state = sr.get_actor(actor_id) if sr is not None else None
            if sr is not None and state is not None:
                self._planetary._save_actor(state, sr.society.society_id)
        except Exception:
            logger.debug("_refresh_registry(%r) failed (non-fatal)", actor_id, exc_info=True)

    # ── Shutdown safety (Section 10, best-effort) ───────────────────────

    def _warn_if_negotiation_pending(self, actor_id: str) -> None:
        """Best-effort in-flight-work signal: negotiation_store.py and
        execution_checkpoint_store.py are keyed by execution_id, not
        actor_id, so there is no general "list every outstanding
        commitment for this actor" query in the current architecture —
        documented as a known limitation (docs/ACTOR_LIFECYCLE.md), not
        silently ignored. What IS checked: whether this actor's own last
        tick result shows an unresolved requires_negotiation action, the
        one signal already resident on ActorRuntimeState with no extra
        query needed. Never raises — duck-typed against whatever shape
        the tick result actually has."""
        try:
            sr = self._planetary._home_society_runtime(actor_id)
            state = sr.get_actor(actor_id) if sr is not None else None
            last_result = getattr(state, "last_tick_result", None) if state is not None else None
            actions = getattr(last_result, "actions", None) or []
            for action in actions:
                result = action.get("result") if isinstance(action, dict) else getattr(action, "result", None)
                if isinstance(result, dict) and result.get("requires_negotiation"):
                    logger.warning(
                        "terminate_actor(%r): last tick left a negotiation pending — "
                        "terminating anyway (best-effort check only)",
                        actor_id,
                    )
                    return
        except Exception:
            logger.debug(
                "_warn_if_negotiation_pending(%r) check failed (non-fatal)",
                actor_id,
                exc_info=True,
            )

    # ── Events + history ─────────────────────────────────────────────────

    def _publish(
        self,
        actor_id: str,
        event_type: LifecycleEventType,
        previous_state: str,
        new_state: str,
        reason: str,
    ) -> None:
        event = LifecycleEvent(
            actor_id=actor_id,
            event_type=event_type,
            previous_state=previous_state,
            new_state=new_state,
            reason=reason,
        )
        try:
            from src.monkey_brain.kernel.society.context_stream import (
                ContextEvent,
                ContextEventType,
            )

            description = f"{actor_id}: {event_type.value} ({previous_state} → {new_state})"
            if reason:
                description += f" — {reason}"
            self._planetary.context_stream.publish(
                ContextEvent(
                    event_type=ContextEventType.ACTOR_LIFECYCLE,
                    actor_id=actor_id,
                    description=description,
                    payload={
                        "event_type": event_type.value,
                        "previous_state": previous_state,
                        "new_state": new_state,
                        "reason": reason,
                    },
                    provenance="actor_lifecycle_controller",
                )
            )
        except Exception:
            logger.debug("_publish: context_stream publish failed (non-fatal)", exc_info=True)
        try:
            from src.monkey_brain.kernel.pipeline.audit_trail import (
                record_decision_event,
            )

            record_decision_event(
                f"actor_lifecycle:{event_type.value}",
                actor_id=actor_id,
                reason=reason,
                metadata={
                    "previous_state": previous_state,
                    "new_state": new_state,
                    "source": "ActorLifecycleController",
                },
            )
        except Exception:
            logger.debug("_publish: audit_trail record failed (non-fatal)", exc_info=True)

    def lifecycle_history(self, actor_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Every recorded lifecycle transition for actor_id, oldest first
        — reconstructed from TimelineStore (Redis-backed, already the
        durable home for cross-cutting decision events; not a new store),
        the same DECISION timeline record_decision_event already writes
        idempotency/payment decisions to."""
        try:
            from src.monkey_brain.kernel.timeline.entry import TimelineKind
            from src.monkey_brain.kernel.timeline.store import TimelineStore

            entries = TimelineStore().query(actor_id, TimelineKind.DECISION)
        except Exception as exc:
            logger.debug("lifecycle_history(%r) query failed: %s", actor_id, exc)
            return []
        history = [
            e.to_dict() for e in entries if str(getattr(e, "selected_strategy", "")).startswith("actor_lifecycle:")
        ]
        history.sort(key=lambda d: d.get("start_time", 0))
        return history[-limit:]
