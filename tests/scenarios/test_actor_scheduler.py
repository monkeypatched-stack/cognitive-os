"""Actor Scheduler — comprehensive qualification tests.

Covers the task's own required-scenario checklist:
  node registration/discovery              -> test_01, test_02
  unmanaged mode (no nodes = unconstrained) -> test_03
  basic placement onto the sole node        -> test_04
  hard constraint: capability filter        -> test_05
  hard constraint: node_class filter        -> test_06
  hard constraint: capacity exhausted       -> test_07
  soft preference: node_class ranking       -> test_08
  soft preference: region ranking           -> test_09
  deterministic tiebreak                    -> test_10
  UNSCHEDULABLE is explicit, not fabricated -> test_11
  idempotent re-scheduling                  -> test_12
  node heartbeat staleness -> excluded      -> test_13
  explicitly unhealthy node -> excluded     -> test_14
  multi-actor capacity, no over-allocation  -> test_15
  concurrency safety (simultaneous decide)  -> test_16
  Lifecycle Controller integration: start deferred to the scheduled node
                                             -> test_17
  Lifecycle Controller integration: UNSCHEDULABLE actor is not started
                                             -> test_18
  migration: checkpoint + local suspend, desired_state untouched
                                             -> test_19
  migration end-to-end: target node resumes the evacuated actor
                                             -> test_20
  destructive: node failure -> reschedule WITHOUT a new actor identity,
  no duplicate registry entry               -> test_21
  scheduler never mutates actor cognition/process (Section 5/12)
                                             -> test_22
  scheduler capacity release on termination -> test_23

Uses a self-contained _FakeRedis (same minimal-subset-of-redis-py shape as
tests/scenarios/test_actor_lifecycle_controller.py's own fake, extended
with real semantics for PlanetaryRuntime._RESERVE_NODE_CAPACITY_SCRIPT —
the one EVAL pattern that fake didn't need to emulate) so node/placement
concurrency behavior is deterministic without a real Redis server.

Per this repo's session convention, this file is written but not executed
by the assistant. Run with:
    python -m pytest tests/scenarios/test_actor_scheduler.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time

import pytest

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_RPS"] = "100000"
os.environ["RATE_LIMIT_BURST"] = "200000"

from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
    ActorStatus,
)
from src.monkey_brain.kernel.society.actor_lifecycle import ActorDesiredState
from src.monkey_brain.kernel.society.actor_scheduler import (
    ExecutionNode,
    NodeClass,
    NodeHealth,
    ActorPlacementRequirements,
)
from src.monkey_brain.kernel.society.context_stream import ContextEventType

# ── Test doubles ─────────────────────────────────────────────────────────


class _FakeRedis:
    """Minimal in-memory stand-in for the subset of redis-py's API this
    module's registries actually call: plain SET/GET/EXISTS/DELETE (desired
    state / desired node / placement requirements / lease), HSET/HGET/
    HGETALL/HDEL (actor + node hashes), and EVAL for the two atomic
    scripts this session's work relies on -- the lease's compare-and-
    delete, and the node-capacity reservation's read-check-write. Real
    Redis executes EVAL server-side, single-threaded; a threading.Lock
    here reproduces that atomicity for the real-OS-thread concurrency
    test below (test_16), since the GIL alone does not make a multi-
    bytecode check-then-write sequence atomic across threads."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expiry: dict[str, float] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    def _expired(self, key: str) -> bool:
        exp = self._expiry.get(key)
        return exp is not None and time.time() > exp

    def ping(self) -> bool:
        return True

    def set(self, key, value, nx=False, ex=None):
        with self._lock:
            if self._expired(key):
                self._store.pop(key, None)
            if nx and key in self._store:
                return False
            self._store[key] = value
            if ex is not None:
                self._expiry[key] = time.time() + ex
            else:
                self._expiry.pop(key, None)
            return True

    def get(self, key):
        if self._expired(key):
            self._store.pop(key, None)
            return None
        return self._store.get(key)

    def exists(self, key):
        return 1 if self.get(key) is not None else 0

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                n += 1
        return n

    def hset(self, name, key, value):
        self._hashes.setdefault(name, {})[key] = value

    def hget(self, name, key):
        return self._hashes.get(name, {}).get(key)

    def hgetall(self, name):
        return dict(self._hashes.get(name, {}))

    def hdel(self, name, key):
        self._hashes.get(name, {}).pop(key, None)

    def eval(self, script, numkeys, key, *args):
        if "cjson" in script:
            # PlanetaryRuntime._RESERVE_NODE_CAPACITY_SCRIPT semantics.
            node_id, delta, ts = args
            with self._lock:
                hashes = self._hashes.get(key, {})
                raw = hashes.get(node_id)
                if raw is None:
                    return -1
                node = json.loads(raw)
                delta = int(delta)
                capacity = int(node.get("capacity", 0))
                current = int(node.get("current_actor_count", 0))
                new_count = current + delta
                if delta > 0 and new_count > capacity:
                    return -2
                new_count = max(0, new_count)
                node["current_actor_count"] = new_count
                node["updated_at"] = float(ts)
                hashes[node_id] = json.dumps(node)
                self._hashes[key] = hashes
                return new_count
        # _RELEASE_LOCK_IF_OWNER_SCRIPT semantics (actor lease release).
        token = args[0] if args else None
        with self._lock:
            if self._store.get(key) == token and not self._expired(key):
                del self._store[key]
                return 1
            return 0


def _register(pr: PlanetaryRuntime, name: str, **kwargs):
    return pr.register_actor(
        ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.AI_AGENT)),
        **kwargs,
    )


def _lifecycle_events(pr: PlanetaryRuntime, actor_id: str) -> list[dict]:
    return [
        e.payload
        for e in pr.context_stream._events
        if e.event_type == ContextEventType.ACTOR_LIFECYCLE and e.actor_id == actor_id
    ]


# ── 1-2: Node registration and discovery ─────────────────────────────────


def test_01_register_and_get_node_round_trips():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    node = ExecutionNode(
        node_id="n1",
        node_class=NodeClass.EDGE,
        capacity=5,
        capabilities=("gpu",),
        region="us-east",
    )
    pr.register_node(node)
    fetched = pr.get_node("n1")
    assert fetched is not None
    assert fetched.node_id == "n1"
    assert fetched.node_class == NodeClass.EDGE
    assert fetched.capacity == 5
    assert fetched.capabilities == ("gpu",)
    assert fetched.region == "us-east"


def test_02_list_nodes_returns_every_registered_node():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="n1"))
    pr.register_node(ExecutionNode(node_id="n2"))
    ids = {n.node_id for n in pr.list_nodes()}
    assert ids == {"n1", "n2"}
    pr.deregister_node("n1")
    assert {n.node_id for n in pr.list_nodes()} == {"n2"}


# ── 3: Unmanaged mode ─────────────────────────────────────────────────────


def test_03_no_nodes_registered_leaves_placement_unconstrained():
    pr = PlanetaryRuntime()
    state = _register(pr, "solo")
    decision = pr.scheduler.schedule(state.actor_id)
    assert decision.scheduled is True
    assert decision.node_id == ""
    # And the Lifecycle Controller still starts the actor normally --
    # zero nodes registered must never regress today's implicit
    # single-process behavior (every pre-existing lifecycle test in this
    # repo never registers a node at all).
    result = pr.lifecycle.reconcile(state.actor_id)
    assert result.action == "start"
    assert result.succeeded is True


# ── 4: Basic placement ────────────────────────────────────────────────────


def test_04_schedules_onto_the_sole_healthy_node():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="only", capacity=10))
    state = _register(pr, "alice")
    decision = pr.scheduler.schedule(state.actor_id)
    assert decision.scheduled is True
    assert decision.node_id == "only"
    assert pr.get_actor_desired_node(state.actor_id) == "only"


# ── 5-7: Hard constraints ──────────────────────────────────────────────────


def test_05_required_capability_eliminates_nonmatching_node():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="plain", capacity=10, capabilities=()))
    pr.register_node(ExecutionNode(node_id="gpu-node", capacity=10, capabilities=("gpu",)))
    state = _register(pr, "trainer")
    decision = pr.scheduler.schedule(state.actor_id, ActorPlacementRequirements(required_capabilities=("gpu",)))
    assert decision.scheduled is True
    assert decision.node_id == "gpu-node"
    rejected_ids = {n for n, _ in decision.candidates_rejected}
    assert "plain" in rejected_ids


def test_06_required_node_class_eliminates_nonmatching_node():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="cloud1", node_class=NodeClass.CLOUD, capacity=10))
    pr.register_node(ExecutionNode(node_id="edge1", node_class=NodeClass.EDGE, capacity=10))
    state = _register(pr, "local_agent")
    decision = pr.scheduler.schedule(state.actor_id, ActorPlacementRequirements(required_node_class=NodeClass.EDGE))
    assert decision.scheduled is True
    assert decision.node_id == "edge1"


def test_07_insufficient_capacity_eliminates_full_node():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="full", capacity=1, current_actor_count=1))
    pr.register_node(ExecutionNode(node_id="room", capacity=1, current_actor_count=0))
    state = _register(pr, "bob")
    decision = pr.scheduler.schedule(state.actor_id)
    assert decision.scheduled is True
    assert decision.node_id == "room"


# ── 8-9: Soft preferences ──────────────────────────────────────────────────


def test_08_preferred_node_class_wins_ranking_among_valid_candidates():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="cloud1", node_class=NodeClass.CLOUD, capacity=10))
    pr.register_node(ExecutionNode(node_id="edge1", node_class=NodeClass.EDGE, capacity=10))
    state = _register(pr, "prefers_edge")
    decision = pr.scheduler.schedule(state.actor_id, ActorPlacementRequirements(preferred_node_class=NodeClass.EDGE))
    assert decision.node_id == "edge1"


def test_09_preferred_region_wins_ranking_among_valid_candidates():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="us", capacity=10, region="us-east"))
    pr.register_node(ExecutionNode(node_id="eu", capacity=10, region="eu-west"))
    state = _register(pr, "prefers_eu")
    decision = pr.scheduler.schedule(state.actor_id, ActorPlacementRequirements(preferred_region="eu-west"))
    assert decision.node_id == "eu"


# ── 10: Deterministic tiebreak ────────────────────────────────────────────


def test_10_equal_candidates_break_tie_on_node_id_deterministically():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="z-node", capacity=10))
    pr.register_node(ExecutionNode(node_id="a-node", capacity=10))
    ids = [pr.scheduler.schedule(_register(pr, f"actor{i}").actor_id).node_id for i in range(3)]
    assert ids == ["a-node"] * 3  # repeated, independent decisions all agree


# ── 11: UNSCHEDULABLE is explicit, never fabricated ──────────────────────


def test_11_no_qualifying_node_reports_unschedulable_not_a_fake_placement():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="plain", capacity=10, capabilities=()))
    state = _register(pr, "needs_gpu")
    decision = pr.scheduler.schedule(state.actor_id, ActorPlacementRequirements(required_capabilities=("gpu",)))
    assert decision.scheduled is False
    assert decision.node_id == ""
    assert "gpu" in decision.reason or "capabilities" in decision.reason
    assert decision.candidates_rejected == (("plain", "missing required capabilities: ['gpu']"),)
    assert pr.get_actor_desired_node(state.actor_id) == ""  # never wrote a fake placement


# ── 12: Idempotency ────────────────────────────────────────────────────────


def test_12_repeated_scheduling_of_a_settled_actor_keeps_the_same_node():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="n1", capacity=10))
    pr.register_node(ExecutionNode(node_id="n2", capacity=10))
    state = _register(pr, "stable")
    first = pr.scheduler.schedule(state.actor_id)
    second = pr.scheduler.schedule(state.actor_id)
    assert first.node_id == second.node_id
    assert "already validly placed" in second.reason


# ── 13-14: Node health ─────────────────────────────────────────────────────


def test_13_stale_heartbeat_treated_as_unknown_and_excluded():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    stale = ExecutionNode(node_id="stale", capacity=10, updated_at=time.time() - 10_000)
    pr.register_node(stale)
    pr.register_node(ExecutionNode(node_id="fresh", capacity=10))
    fetched = pr.get_node("stale")
    assert fetched.reported_health == NodeHealth.UNKNOWN
    state = _register(pr, "carol")
    decision = pr.scheduler.schedule(state.actor_id)
    assert decision.node_id == "fresh"


def test_14_explicitly_unhealthy_node_excluded():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="sick", capacity=10, reported_health=NodeHealth.UNHEALTHY))
    pr.register_node(ExecutionNode(node_id="ok", capacity=10))
    state = _register(pr, "dave")
    decision = pr.scheduler.schedule(state.actor_id)
    assert decision.node_id == "ok"
    assert ("sick", "node is not healthy") in decision.candidates_rejected


# ── 15: Multi-actor capacity enforcement ──────────────────────────────────


def test_15_capacity_enforced_across_multiple_actors_no_overallocation():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="small", capacity=2))
    ids = [_register(pr, f"a{i}").actor_id for i in range(3)]
    decisions = [pr.scheduler.schedule(aid) for aid in ids]
    scheduled = [d for d in decisions if d.scheduled]
    assert len(scheduled) == 2  # capacity 2 -- the third must be unschedulable
    unscheduled = [d for d in decisions if not d.scheduled]
    assert len(unscheduled) == 1
    node = pr.get_node("small")
    assert node.current_actor_count == 2  # never over-allocated past capacity


# ── 16: Concurrency safety ────────────────────────────────────────────────


def test_16_concurrent_scheduling_decisions_never_overallocate():
    """Two real OS threads race to schedule two different actors onto a
    single-slot node at the same moment -- exactly one must win; the
    _RESERVE_NODE_CAPACITY_SCRIPT's atomic EVAL (not a Python read-then-
    write) is what makes this deterministic rather than flaky."""
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="one-slot", capacity=1))
    a1 = _register(pr, "racer1").actor_id
    a2 = _register(pr, "racer2").actor_id

    results = {}

    def _schedule(actor_id, key):
        results[key] = pr.scheduler.schedule(actor_id)

    t1 = threading.Thread(target=_schedule, args=(a1, "r1"))
    t2 = threading.Thread(target=_schedule, args=(a2, "r2"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    scheduled = [r for r in results.values() if r.scheduled]
    assert len(scheduled) == 1
    node = pr.get_node("one-slot")
    assert node.current_actor_count == 1


# ── 17-18: Lifecycle Controller integration ───────────────────────────────


def test_17_start_is_deferred_to_the_scheduled_node_not_started_locally():
    """Actor identity != actor location: pr_a and pr_b share one registry
    (same fake Redis) and represent two different nodes. The actor is
    explicitly placed on node-b. Reconciling from node-a must NOT start
    it locally; reconciling from node-b must."""
    shared = _FakeRedis()
    pr_a = PlanetaryRuntime()
    pr_a._redis = shared
    pr_a._node_id = "node-a"
    pr_b = PlanetaryRuntime()
    pr_b._redis = shared
    pr_b._node_id = "node-b"
    pr_a.register_node(ExecutionNode(node_id="node-a", capacity=10))
    pr_a.register_node(ExecutionNode(node_id="node-b", capacity=10))

    state = _register(pr_a, "placed_actor")
    pr_a.set_actor_desired_node(state.actor_id, "node-b")
    pr_a._reserve_node_capacity("node-b", 1)

    result_a = pr_a.lifecycle.reconcile(state.actor_id)
    assert result_a.action == "scheduled_elsewhere"
    assert result_a.succeeded is True
    assert pr_a.observe_actor(state.actor_id).status != ActorStatus.ACTIVE.value

    result_b = pr_b.lifecycle.reconcile(state.actor_id)
    assert result_b.action == "start"
    assert result_b.succeeded is True
    sr = pr_b._home_society_runtime(state.actor_id)
    assert sr.get_actor(state.actor_id).status == ActorStatus.ACTIVE


def test_18_unschedulable_actor_is_never_started():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="plain", capacity=10, capabilities=()))
    state = _register(pr, "needs_gpu_actor")
    pr.set_actor_placement_requirements(state.actor_id, ActorPlacementRequirements(required_capabilities=("gpu",)))

    result = pr.lifecycle.reconcile(state.actor_id)
    assert result.action == "unschedulable"
    assert result.succeeded is False
    observed = pr.observe_actor(state.actor_id)
    assert observed.status != ActorStatus.ACTIVE.value
    events = [e["event_type"] for e in _lifecycle_events(pr, state.actor_id)]
    assert "actor_unschedulable" in events


# ── 19-20: Migration ───────────────────────────────────────────────────────


def test_19_migrate_checkpoints_and_suspends_locally_desired_state_unchanged():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="here", capacity=10))
    pr.register_node(ExecutionNode(node_id="there", capacity=10))
    state = _register(pr, "migrator")
    # Learn which node the Scheduler will pick BEFORE reconciling, then
    # make this process actually BE that node -- reconcile()'s
    # _consult_scheduler otherwise correctly defers to "scheduled
    # elsewhere" for a process whose own _node_id doesn't match the
    # decision, and this test needs a genuine local start to verify the
    # local-suspend half of migration below.
    placed_node = pr.scheduler.schedule(state.actor_id).node_id
    pr._node_id = placed_node
    start_result = pr.lifecycle.reconcile(state.actor_id)
    assert start_result.action == "start"
    assert start_result.succeeded is True

    desired_before = pr.get_actor_desired_state(state.actor_id)
    decision = pr.scheduler.migrate_actor(state.actor_id, target_node_id="there" if placed_node == "here" else "here")
    assert decision.scheduled is True
    assert pr.get_actor_desired_state(state.actor_id) == desired_before  # RUNNING, untouched
    sr = pr._home_society_runtime(state.actor_id)
    assert sr.get_actor(state.actor_id).status == ActorStatus.SUSPENDED


def test_20_migration_end_to_end_target_node_resumes_the_actor():
    shared = _FakeRedis()
    pr_a = PlanetaryRuntime()
    pr_a._redis = shared
    pr_a._node_id = "node-a"
    pr_b = PlanetaryRuntime()
    pr_b._redis = shared
    pr_b._node_id = "node-b"
    pr_a.register_node(ExecutionNode(node_id="node-a", capacity=10))
    pr_a.register_node(ExecutionNode(node_id="node-b", capacity=10))

    state = _register(pr_a, "migrating_actor")
    pr_a.set_actor_desired_node(state.actor_id, "node-a")
    pr_a._reserve_node_capacity("node-a", 1)
    start_result = pr_a.lifecycle.reconcile(state.actor_id)
    assert start_result.action == "start"

    migrate_decision = pr_a.scheduler.migrate_actor(state.actor_id, target_node_id="node-b")
    assert migrate_decision.node_id == "node-b"
    # pr_a evacuated it locally (suspend_actor_for_migration acted here).
    sr_a = pr_a._home_society_runtime(state.actor_id)
    assert sr_a.get_actor(state.actor_id).status == ActorStatus.SUSPENDED

    resume_result = pr_b.lifecycle.reconcile(state.actor_id)
    assert resume_result.action == "resume"
    assert resume_result.succeeded is True
    sr_b = pr_b._home_society_runtime(state.actor_id)
    assert sr_b.get_actor(state.actor_id).status == ActorStatus.ACTIVE
    assert pr_b.get_actor_desired_state(state.actor_id) == ActorDesiredState.RUNNING


# ── 21: Destructive — node failure -> reschedule, identity preserved ─────


def test_21_node_failure_reschedules_without_new_identity_or_duplication():
    """The critical invariant: a dead node must never spawn a second
    actor identity. node-a hosts the actor, then goes dark (registry
    record ages past staleness, node deregistered). node-b picks up
    recovery -- same actor_id, exactly one registry entry, no ghost."""
    shared = _FakeRedis()
    pr_a = PlanetaryRuntime()
    pr_a._redis = shared
    pr_a._node_id = "node-a"
    pr_b = PlanetaryRuntime()
    pr_b._redis = shared
    pr_b._node_id = "node-b"
    pr_a.register_node(ExecutionNode(node_id="node-a", capacity=10))

    state = _register(pr_a, "survivor")
    original_actor_id = state.actor_id
    result = pr_a.lifecycle.reconcile(original_actor_id)
    assert result.action == "start"
    assert pr_a.locate_actor(original_actor_id).node_id == "node-a"

    # Simulate node-a dying without a clean shutdown: age its actor
    # registry record well past the staleness threshold.
    raw = json.loads(shared.hget(pr_a._ACTORS_HASH_KEY, original_actor_id))
    raw["updated_at"] = time.time() - (pr_a._ACTOR_STALE_SECONDS + 100)
    shared.hset(pr_a._ACTORS_HASH_KEY, original_actor_id, json.dumps(raw))

    # Infra/operator detects the dead node and removes it, registering a
    # replacement.
    pr_b.deregister_node("node-a")
    pr_b.register_node(ExecutionNode(node_id="node-b", capacity=10))

    observed = pr_b.observe_actor(original_actor_id)
    assert observed.is_stale is True

    recover_result = pr_b.lifecycle.reconcile(original_actor_id)
    assert recover_result.action == "recover"
    assert recover_result.succeeded is True

    # Identity preserved.
    assert recover_result.actor_id == original_actor_id
    # Exactly one registry entry -- no duplicate/ghost actor.
    all_ids = [e.actor_id for e in pr_b.list_registry()]
    assert all_ids.count(original_actor_id) == 1
    # Placement now reflects the surviving node.
    entry = pr_b.locate_actor(original_actor_id)
    assert entry.node_id == "node-b"
    assert entry.status == ActorStatus.ACTIVE.value
    assert pr_b.get_actor_desired_node(original_actor_id) == "node-b"


# ── 22: Scheduler never mutates actor cognition ───────────────────────────


def test_22_scheduling_alone_never_touches_actor_runtime_state():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="n1", capacity=10))
    state = _register(pr, "untouched")
    assert state.status == ActorStatus.REGISTERED
    assert state.is_active is False

    pr.scheduler.schedule(state.actor_id)  # placement decision only

    sr = pr._home_society_runtime(state.actor_id)
    fresh = sr.get_actor(state.actor_id)
    assert fresh.status == ActorStatus.REGISTERED  # unchanged -- never started
    assert fresh.is_active is False


# ── 23: Capacity released on termination ──────────────────────────────────


def test_23_terminating_an_actor_releases_its_capacity_reservation():
    pr = PlanetaryRuntime()
    pr._redis = _FakeRedis()
    pr.register_node(ExecutionNode(node_id="n1", capacity=1))
    state = _register(pr, "temp")
    pr.lifecycle.reconcile(state.actor_id)
    assert pr.get_node("n1").current_actor_count == 1

    pr.lifecycle.set_desired_state(state.actor_id, ActorDesiredState.TERMINATED)
    result = pr.lifecycle.reconcile(state.actor_id)
    assert result.action == "terminate"
    assert pr.get_node("n1").current_actor_count == 0

    # The freed slot is usable by a new actor.
    other = _register(pr, "replacement")
    decision = pr.scheduler.schedule(other.actor_id)
    assert decision.scheduled is True
    assert decision.node_id == "n1"
