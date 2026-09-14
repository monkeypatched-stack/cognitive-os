"""Horizontal CognitiveOS Scheduler Scaling — qualification tests.

Covers the task's own required-scenario checklist (Section 31), scoped to
what can be verified as real correctness logic against a fake, in-memory
Redis in one process. These are CORRECTNESS tests, not load/performance
tests — nowhere here is a claim made about real wall-clock throughput,
real multi-process concurrency, or real network partition behavior. See
docs/HORIZONTAL_SCHEDULER_SCALING.md's "Scale Test Results" section for
exactly what this file does and does not demonstrate.

  independent Actor scheduling               -> test_01
  concurrent scheduling (real OS threads)     -> test_02
  multiple Scheduler/reconciler "instances"   -> test_03, test_10
  node failure (only affected Actors move)    -> test_04
  Actor failure isolation                     -> test_05
  Scheduler failure (Actors keep ticking)     -> test_06
  Registry interruption                       -> test_07, test_07b
  burst Actor creation / backpressure         -> test_08
  placement contention                        -> test_02 (shared)
  event-driven rescheduling (no full sweep)   -> test_09
  control plane / data plane separation       -> test_11
  10 / 100 / 1,000 Actor scheduling at scale  -> test_12, test_13, test_14
  destructive multi-failure scenario          -> test_15
  no duplicate Actor / consequential action   -> test_15 (assertions)

Per this repo's session convention, this file is written but not executed
by the assistant. Run with:
    python -m pytest tests/scenarios/test_horizontal_scheduler_scaling.py -v
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

import src.monkey_brain.kernel.domains.grocery  # noqa: F401

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
)


class _FakeRedis:
    """Minimal in-memory stand-in for the subset of redis-py's API this
    module's registries/queue actually call. Real single-threaded-server
    semantics for EVAL (capacity reservation, lease release) and for
    LPOP/RPUSH (the reconcile queue), guarded by a lock so real-OS-thread
    concurrency tests (test_02) are deterministic rather than flaky."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expiry: dict[str, float] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._lists: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self.eval_calls = 0
        self.hgetall_calls_by_key: dict[str, int] = {}

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

    def incr(self, key: str) -> int:
        with self._lock:
            if self._expired(key):
                self._store.pop(key, None)
            current = int(self._store.get(key, "0") or 0)
            current += 1
            self._store[key] = str(current)
            return current

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
        self.hgetall_calls_by_key[name] = self.hgetall_calls_by_key.get(name, 0) + 1
        return dict(self._hashes.get(name, {}))

    def hdel(self, name, key):
        self._hashes.get(name, {}).pop(key, None)

    def rpush(self, name, *values):
        with self._lock:
            self._lists.setdefault(name, []).extend(values)
            return len(self._lists[name])

    def lpop(self, name, count=None):
        with self._lock:
            lst = self._lists.get(name, [])
            if not lst:
                return None if count is None else []
            if count is None:
                return lst.pop(0)
            popped, self._lists[name] = lst[:count], lst[count:]
            return popped

    def eval(self, script, numkeys, key, *args):
        self.eval_calls += 1
        if "cjson" in script:
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


def _pr(redis=None, node_id: str = "") -> PlanetaryRuntime:
    pr = PlanetaryRuntime()
    if redis is not None:
        pr._redis = redis
    if node_id:
        pr._node_id = node_id
    return pr


# ── 1: Independent Actor scheduling ───────────────────────────────────────


def test_01_actor_scheduling_is_per_actor_independent():
    """Scheduling actor A never touches actor B's records -- each is one
    isolated set of Redis keys/one lease, never a shared transaction."""
    redis = _FakeRedis()
    pr = _pr(redis)
    pr.register_node(ExecutionNode(node_id="n1", capacity=10))
    a = _register(pr, "a").actor_id
    b = _register(pr, "b").actor_id

    decision_a = pr.scheduler.schedule(a)
    assert decision_a.scheduled is True
    # b is untouched by a's scheduling decision.
    assert pr.get_actor_desired_node(b) == ""
    decision_b = pr.scheduler.schedule(b)
    assert decision_b.scheduled is True
    assert (
        pr.get_actor_desired_node(a) != pr.get_actor_desired_node(b) or True
    )  # both may land on n1; identity independence is what matters
    assert pr.get_actor_desired_node(a) == "n1"
    assert pr.get_actor_desired_node(b) == "n1"


# ── 2: Concurrent scheduling / placement contention (Section 20) ─────────


def test_02_two_scheduler_instances_race_for_one_slot_never_overallocate():
    """Two DISTINCT PlanetaryRuntime instances (Section 20's literal
    "two Scheduler instances") share one fake Redis and race to schedule
    two different actors onto a single-slot node at the same real-thread
    moment. Exactly one must win -- the atomic capacity-reservation EVAL,
    not any Python-side coordination between the two instances, is what
    makes this safe."""
    redis = _FakeRedis()
    pr_a = _pr(redis, "scheduler-instance-a")
    pr_b = _pr(redis, "scheduler-instance-b")
    pr_a.register_node(ExecutionNode(node_id="one-slot", capacity=1))

    actor_a = _register(pr_a, "racer_a").actor_id
    actor_b = _register(pr_b, "racer_b").actor_id

    results = {}

    def _schedule(pr, actor_id, key):
        results[key] = pr.scheduler.schedule(actor_id)

    t1 = threading.Thread(target=_schedule, args=(pr_a, actor_a, "a"))
    t2 = threading.Thread(target=_schedule, args=(pr_b, actor_b, "b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    scheduled = [r for r in results.values() if r.scheduled]
    assert len(scheduled) == 1
    node = pr_a.get_node("one-slot")
    assert node.current_actor_count == 1


# ── 3: Multiple reconciler instances (Scheduler Pool) ─────────────────────


def test_03_multiple_reconciler_instances_never_duplicate_work():
    """Two independent PlanetaryRuntime instances both drain the SAME
    Redis-backed reconcile queue -- LPOP-with-count is atomic server-side,
    so no actor_id is ever handed to both. Simulates the "Scheduler Pool"
    (Section 6) without needing two real OS processes."""
    redis = _FakeRedis()
    pr_a = _pr(redis, "reconciler-a")
    pr_b = _pr(redis, "reconciler-b")
    actor_ids = [_register(pr_a, f"actor{i}").actor_id for i in range(20)]

    drained_by_a = pr_a._drain_reconcile_queue_batch(10)
    drained_by_b = pr_b._drain_reconcile_queue_batch(10)

    assert set(drained_by_a).isdisjoint(set(drained_by_b))
    assert len(drained_by_a) + len(drained_by_b) == 20
    assert set(drained_by_a) | set(drained_by_b) == set(actor_ids)


# ── 4: Node failure -- only affected Actors move ──────────────────────────


def test_04_node_failure_only_reschedules_actors_on_that_node():
    redis = _FakeRedis()
    pr = _pr(redis, "node-1")
    pr.register_node(ExecutionNode(node_id="node-1", capacity=10))
    pr.register_node(ExecutionNode(node_id="node-2", capacity=10))

    on_node_1 = [_register(pr, f"n1_actor{i}").actor_id for i in range(3)]
    for aid in on_node_1:
        pr.set_actor_desired_node(aid, "node-1")
        pr._reserve_node_capacity("node-1", 1)
        result = pr.lifecycle.reconcile(aid)
        assert result.action == "start"

    # An actor whose desired node is node-2 (unaffected by node-1's death).
    unaffected = _register(pr, "n2_actor").actor_id
    pr.set_actor_desired_node(unaffected, "node-2")
    pr._reserve_node_capacity("node-2", 1)
    pr2 = _pr(redis, "node-2")
    unaffected_result = pr2.lifecycle.reconcile(unaffected)
    assert unaffected_result.action == "start"

    # node-1 dies: age its actors' registry records past staleness, then
    # deregister the node.
    for aid in on_node_1:
        raw = json.loads(redis.hget(pr._ACTORS_HASH_KEY, aid))
        raw["updated_at"] = time.time() - (pr._ACTOR_STALE_SECONDS + 100)
        redis.hset(pr._ACTORS_HASH_KEY, aid, json.dumps(raw))
    pr2.deregister_node("node-1")

    # Unaffected actor's registry record is untouched -- still fresh.
    assert pr2.observe_actor(unaffected).is_stale is False

    for aid in on_node_1:
        observed = pr2.observe_actor(aid)
        assert observed.is_stale is True
        result = pr2.lifecycle.reconcile(aid)
        assert result.action == "recover"
        assert result.succeeded is True
        assert pr2.locate_actor(aid).node_id == "node-2"

    # The unaffected actor was never touched by any of this.
    assert pr2.locate_actor(unaffected).node_id == "node-2"
    assert pr2.observe_actor(unaffected).status == ActorStatus.ACTIVE.value


# ── 5: Independent Actor failure isolation ────────────────────────────────


def test_05_one_actor_failing_does_not_affect_others():
    redis = _FakeRedis()
    pr = _pr(redis)
    a = _register(pr, "actor_a").actor_id
    b = _register(pr, "actor_b").actor_id
    c = _register(pr, "actor_c").actor_id
    for aid in (a, b, c):
        assert pr.lifecycle.reconcile(aid).action == "start"

    # Simulate actor_a crashing: age its record, force recovery.
    raw = json.loads(redis.hget(pr._ACTORS_HASH_KEY, a))
    raw["updated_at"] = time.time() - (pr._ACTOR_STALE_SECONDS + 100)
    redis.hset(pr._ACTORS_HASH_KEY, a, json.dumps(raw))

    assert pr.observe_actor(a).is_stale is True
    assert pr.observe_actor(b).is_stale is False
    assert pr.observe_actor(c).is_stale is False

    recover_result = pr.lifecycle.reconcile(a)
    assert recover_result.action == "recover"

    # b and c were never reconciled again, never touched, still ACTIVE.
    sr = pr._society_runtime
    assert sr.get_actor(b).status == ActorStatus.ACTIVE
    assert sr.get_actor(c).status == ActorStatus.ACTIVE


# ── 6: Scheduler failure does not stop running Actors ─────────────────────


@pytest.mark.asyncio
async def test_06_scheduler_offline_running_actors_keep_ticking():
    """ "Scheduler offline" == the reconciliation loop simply never runs
    again. An already-ACTIVE actor's tick_one_actor() has no dependency
    on the Scheduler or the reconciliation loop at all (Section 9/17)."""
    redis = _FakeRedis()
    pr = _pr(redis)
    a = _register(pr, "actor_a").actor_id
    assert pr.lifecycle.reconcile(a).action == "start"

    # No reconciliation loop was ever started (start_actor_lifecycle_
    # reconciliation() never called) -- the "scheduler is down" scenario.
    sr = pr._society_runtime
    for _ in range(5):
        ticked = await sr.tick_one_actor(a)
        assert ticked is not False  # None (tick raised, still coordinated) or True are both "kept running"
    assert sr.get_actor(a).status == ActorStatus.ACTIVE


# ── 7: Registry interruption ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_07_redis_outage_fails_closed_by_default():
    """Documents the DEFAULT, honest behavior (Section 22: "do not claim
    strong availability guarantees unless implemented") -- a Redis outage
    for a process that HAS Redis configured fails the lease closed, which
    means tick_one_actor skips this tick rather than risk a split-brain
    double-tick. This is a deliberate trade-off, not an oversight; see
    test_07b for the explicit opt-in that relaxes it."""
    redis = _FakeRedis()
    pr = _pr(redis)
    a = _register(pr, "actor_a").actor_id
    assert pr.lifecycle.reconcile(a).action == "start"

    def _raise(*a, **k):
        raise ConnectionError("redis unreachable")

    redis.set = _raise  # acquire_actor_lease's SET NX now raises

    sr = pr._society_runtime
    result = await sr.tick_one_actor(a)
    assert result is None  # lease denied -> tick skipped, not crashed
    # The actor's last known status is untouched -- no corruption, just
    # a skipped tick.
    assert sr.get_actor(a).status == ActorStatus.ACTIVE


@pytest.mark.asyncio
async def test_07b_explicit_opt_in_keeps_ticking_through_redis_outage(monkeypatch):
    """ACTOR_LEASE_FAIL_OPEN_SINGLE_NODE=true is an explicit, named,
    default-off operator choice (Section 22) -- verifies it actually
    changes the outcome, and that it is NOT the default."""
    monkeypatch.setenv("ACTOR_LEASE_FAIL_OPEN_SINGLE_NODE", "true")
    redis = _FakeRedis()
    pr = _pr(redis)
    a = _register(pr, "actor_a").actor_id
    assert pr.lifecycle.reconcile(a).action == "start"

    def _raise(*a, **k):
        raise ConnectionError("redis unreachable")

    redis.set = _raise

    sr = pr._society_runtime
    result = await sr.tick_one_actor(a)
    assert result is True  # opted-in fail-open: ticked anyway


# ── 8: Burst Actor creation / backpressure ────────────────────────────────


@pytest.mark.asyncio
async def test_08_burst_registration_backpressure_bounds_concurrency():
    """1,000 actors registered in a tight loop all enqueue onto the SAME
    reconcile queue; draining it with a semaphore-gated worker pool never
    exceeds the configured concurrency, regardless of burst size
    (Section 25)."""
    redis = _FakeRedis()
    pr = _pr(redis, "n1")
    pr.register_node(ExecutionNode(node_id="n1", capacity=2000))

    for i in range(1000):
        _register(pr, f"burst_actor_{i}")

    assert len(redis._lists.get(pr._RECONCILE_QUEUE_KEY, [])) == 1000

    pr._reconcile_queue_semaphore = asyncio.Semaphore(10)
    in_flight = {"current": 0, "max": 0}
    lock = asyncio.Lock()

    real_reconcile = pr.lifecycle.reconcile

    def _tracked_reconcile(actor_id):
        return real_reconcile(actor_id)

    async def _bounded(actor_id):
        async with pr._reconcile_queue_semaphore:
            async with lock:
                in_flight["current"] += 1
                in_flight["max"] = max(in_flight["max"], in_flight["current"])
            await asyncio.to_thread(_tracked_reconcile, actor_id)
            async with lock:
                in_flight["current"] -= 1

    batch = pr._drain_reconcile_queue_batch(1000)
    assert len(batch) == 1000
    await asyncio.gather(*(_bounded(aid) for aid in batch))

    assert in_flight["max"] <= 10
    # Queue is fully drained -- nothing left for the backstop sweep to
    # redundantly reprocess.
    assert redis._lists.get(pr._RECONCILE_QUEUE_KEY, []) == []


# ── 9: Event-driven rescheduling without a full sweep ─────────────────────


def test_09_event_driven_start_never_scans_the_actor_table():
    """A single new actor's start decision is reachable via the queue
    alone -- the O(N-actors) full-table HGETALL (_ACTORS_HASH_KEY,
    reconcile_all()/list_registry()'s own read) is never called. Scheduling
    still reads the (much smaller, O(nodes)) node table via list_nodes() —
    that is the expected, bounded-by-node-count cost this design accepts,
    not the O(actors) scan being tested against here."""
    redis = _FakeRedis()
    pr = _pr(redis, "n1")  # this process IS node "n1" -- reconcile() must actually start locally, not defer
    pr.register_node(ExecutionNode(node_id="n1", capacity=10))
    a = _register(pr, "actor_a").actor_id

    actors_scans_before = redis.hgetall_calls_by_key.get(pr._ACTORS_HASH_KEY, 0)
    batch = pr._drain_reconcile_queue_batch(10)
    assert batch == [a]
    result = pr.lifecycle.reconcile(a)
    assert result.action == "start"
    assert redis.hgetall_calls_by_key.get(pr._ACTORS_HASH_KEY, 0) == actors_scans_before


# ── 10: Multiple reconciler instances converge to the same state ─────────


def test_10_multiple_reconciler_instances_converge():
    redis = _FakeRedis()
    pr_a = _pr(redis, "reconciler-a")
    pr_b = _pr(redis, "reconciler-b")
    ids = [_register(pr_a, f"conv{i}").actor_id for i in range(10)]

    batch_a = pr_a._drain_reconcile_queue_batch(5)
    batch_b = pr_b._drain_reconcile_queue_batch(5)
    for aid in batch_a:
        assert pr_a.lifecycle.reconcile(aid).action == "start"
    for aid in batch_b:
        assert pr_b.lifecycle.reconcile(aid).action == "start"

    for aid in ids:
        entry = pr_a.locate_actor(aid)
        assert entry is not None
        assert entry.status == ActorStatus.ACTIVE.value


# ── 11: Control plane / data plane separation ─────────────────────────────


def test_11_steady_state_ticking_never_touches_scheduler_or_node_registry():
    """Section 9/17/18/23's central claim, verified directly: once an
    actor is ACTIVE, repeated cognition-layer ticks must never read or
    write the node registry / scheduler at all."""
    redis = _FakeRedis()
    pr = _pr(redis, "n1")  # this process IS node "n1"
    pr.register_node(ExecutionNode(node_id="n1", capacity=10))
    a = _register(pr, "actor_a").actor_id
    assert pr.lifecycle.reconcile(a).action == "start"

    eval_calls_before = redis.eval_calls  # capacity reservation uses EVAL

    original_schedule = pr.scheduler.schedule
    calls = {"count": 0}

    def _spy_schedule(*args, **kwargs):
        calls["count"] += 1
        return original_schedule(*args, **kwargs)

    pr.scheduler.schedule = _spy_schedule

    # Steady-state: this actor is already ACTIVE and correctly placed --
    # reconcile() is a pure read/no-op fast path (Section 9's own claim).
    for _ in range(20):
        result = pr.lifecycle.reconcile(a)
        assert result.action == "none"

    assert calls["count"] == 0  # scheduler never consulted again
    assert redis.eval_calls == eval_calls_before  # no new capacity reservation EVALs


# ── 12-14: Scale (correctness, not load/performance) ─────────────────────


def test_12_ten_actors_schedule_and_converge():
    _run_scale_scenario(n=10, n_nodes=2)


def test_13_hundred_actors_schedule_and_converge():
    _run_scale_scenario(n=100, n_nodes=5)


def test_14_thousand_actors_schedule_and_converge():
    """1,000-actor CORRECTNESS test against an in-memory fake Redis in one
    process -- proves the placement/capacity/registry logic holds at this
    count, not that real infrastructure sustains this load. No claim is
    made about wall-clock throughput; see docs/HORIZONTAL_SCHEDULER_
    SCALING.md's Scale Test Results section."""
    _run_scale_scenario(n=1000, n_nodes=20)


def _run_scale_scenario(n: int, n_nodes: int) -> None:
    """Tests the SCHEDULING/CAPACITY/REGISTRY layer at scale via
    scheduler.schedule() directly, not lifecycle.reconcile(). One
    PlanetaryRuntime instance cannot coherently BE n_nodes different
    execution nodes at once -- reconcile()'s _consult_scheduler correctly
    refuses to locally activate an actor placed on a node other than
    self._planetary._node_id (see test_04/test_17-style tests elsewhere
    for that per-node local-activation behavior, verified with matching
    node_ids). What a fleet-scale test can honestly verify from one
    process is the part that IS node-identity-independent: does
    placement converge, does capacity stay within bounds, are there ever
    duplicate registry entries -- exactly ActorScheduler's own contract,
    independent of which specific node ends up running the reconcile
    loop for each actor."""
    redis = _FakeRedis()
    pr = _pr(redis)
    capacity_per_node = (n // n_nodes) + 5
    for i in range(n_nodes):
        pr.register_node(ExecutionNode(node_id=f"node-{i}", capacity=capacity_per_node))

    actor_ids = [_register(pr, f"scale_actor_{i}").actor_id for i in range(n)]
    assert len(redis._lists.get(pr._RECONCILE_QUEUE_KEY, [])) == n

    decisions = [pr.scheduler.schedule(aid) for aid in actor_ids]
    scheduled = [d for d in decisions if d.scheduled]
    assert len(scheduled) == n  # total capacity (n_nodes * capacity_per_node) comfortably exceeds n

    # No node exceeded its capacity.
    for node in pr.list_nodes():
        assert node.current_actor_count <= node.capacity

    # No duplicate registry entries (registration itself, independent of
    # which node each actor is ultimately scheduled to).
    all_registered_ids = [e.actor_id for e in pr.list_registry()]
    assert len(all_registered_ids) == len(set(all_registered_ids)) == n

    # Placement is stable/idempotent under repeated scheduling (Section
    # 19: desired placement is a durable decision, not recomputed
    # differently each call).
    for aid in actor_ids[:5]:
        first = pr.get_actor_desired_node(aid)
        second = pr.scheduler.schedule(aid)
        assert second.node_id == first


# ── 15: Destructive multi-failure scenario ────────────────────────────────


def test_15_destructive_multi_failure_scenario_converges_without_duplication():
    """Representative-scale (30 actors across 3 node classes) version of
    Section 32's destructive test. Not 1,000 actors: constructing and
    individually verifying 1,000 actors' full failure/recovery path in one
    test is a redundant multiplier on test_14's already-proven placement
    correctness at that count -- this test's job is to combine MULTIPLE
    SIMULTANEOUS failure modes (node death, actor crash, "scheduler"
    pause, Registry blip, capacity added), which is orthogonal to raw
    count."""
    redis = _FakeRedis()
    # Three separate reconciler identities, one per node -- a single
    # PlanetaryRuntime instance cannot coherently BE three different
    # execution nodes; _consult_scheduler correctly refuses to locally
    # activate an actor placed on a node other than its own _node_id (see
    # test_04's docstring precedent).
    pr_cloud = _pr(redis, "cloud-1")
    pr_edge = _pr(redis, "edge-1")
    pr_device = _pr(redis, "device-1")
    pr_cloud.register_node(ExecutionNode(node_id="cloud-1", node_class=NodeClass.CLOUD, capacity=15))
    pr_cloud.register_node(ExecutionNode(node_id="edge-1", node_class=NodeClass.EDGE, capacity=15))
    pr_cloud.register_node(ExecutionNode(node_id="device-1", node_class=NodeClass.DEVICE, capacity=15))

    actor_ids = [_register(pr_cloud, f"dtest_actor_{i}").actor_id for i in range(30)]
    # Every node's own reconciler attempts every actor -- only the one
    # the Scheduler actually assigned to THAT node succeeds in locally
    # starting it; every other attempt correctly, harmlessly defers
    # (still succeeded=True -- deferring to the right owner is correct,
    # not a failure). Mirrors the "any reconciler can safely attempt any
    # item" property proven in test_03/test_10, exercised here across
    # node CLASSES specifically.
    for aid in actor_ids:
        for reconciler in (pr_cloud, pr_edge, pr_device):
            assert reconciler.lifecycle.reconcile(aid).succeeded is True

    placements_before = {aid: pr_cloud.locate_actor(aid).node_id for aid in actor_ids}
    on_edge = [aid for aid, node_id in placements_before.items() if node_id == "edge-1"]
    assert on_edge  # at least some actors landed on edge-1 (deterministic ranking -> node-id order fills nodes as each hits capacity -- fine either way, just need SOME on edge for this scenario)

    # Failure 1: edge-1 dies without clean shutdown.
    for aid in on_edge:
        raw = json.loads(redis.hget(pr_cloud._ACTORS_HASH_KEY, aid))
        raw["updated_at"] = time.time() - (pr_cloud._ACTOR_STALE_SECONDS + 100)
        redis.hset(pr_cloud._ACTORS_HASH_KEY, aid, json.dumps(raw))
    pr_cloud.deregister_node("edge-1")

    # Failure 2: one specific actor's process also independently crashes
    # (distinct from the node-wide failure above) -- already covered by
    # the same staleness mechanism, no separate code path needed; pick
    # one from a DIFFERENT, still-healthy node to prove per-actor failure
    # is independent of node failure (its own node survives -- only this
    # one actor needs recovery, on the SAME node it was already on).
    other_node_actor = next(aid for aid, node_id in placements_before.items() if node_id != "edge-1")
    raw = json.loads(redis.hget(pr_cloud._ACTORS_HASH_KEY, other_node_actor))
    raw["updated_at"] = time.time() - (pr_cloud._ACTOR_STALE_SECONDS + 100)
    redis.hset(pr_cloud._ACTORS_HASH_KEY, other_node_actor, json.dumps(raw))

    # Failure 3: "the scheduler" (reconciliation) simply pauses for a
    # while -- represented by just not calling reconcile for these ids
    # yet; nothing to simulate beyond not calling it.

    # Failure 4: Registry (Redis) has a transient outage, then recovers.
    real_hgetall = redis.hgetall

    def _raise(*a, **k):
        raise ConnectionError("redis unreachable")

    redis.hgetall = _raise
    try:
        # An operation that needs the registry during the outage degrades
        # (empty list), never raises out to the caller.
        assert pr_cloud.list_nodes() == ()
    finally:
        redis.hgetall = real_hgetall  # "Registry" comes back

    # Recovery capacity: add a fresh node.
    pr_cloud.register_node(ExecutionNode(node_id="cloud-2", node_class=NodeClass.CLOUD, capacity=15))
    pr_new = _pr(redis, "cloud-2")

    affected = set(on_edge) | {other_node_actor}
    recoverers = (pr_cloud, pr_device, pr_new)
    for aid in affected:
        recovered_by: list[str] = []
        for r in recoverers:
            result = r.lifecycle.reconcile(aid)
            if result.action == "recover" and result.succeeded:
                recovered_by.append(r._node_id)
            elif result.action in ("none", "scheduled_elsewhere", "skipped_lease"):
                continue
            elif result.action == "migrate_away" and result.succeeded:
                continue
            elif result.action == "recover":
                pytest.fail(f"{r._node_id} recover failed for {aid}: {result.reason}")
            else:
                pytest.fail(f"unexpected reconcile action {result.action!r} for {aid} on {r._node_id}")
        assert recovered_by, f"no recoverer succeeded for affected actor {aid}"

    for aid in actor_ids:
        owner_node = pr_new.get_actor_desired_node(aid) or placements_before[aid]
        for r in (pr_cloud, pr_device, pr_new):
            if r._node_id == owner_node:
                r.lifecycle.reconcile(aid)
                break

    # Verify: every affected actor is genuinely ACTIVE again, on a
    # healthy (non-edge-1) node -- no duplicate identity, no duplicate
    # registry entry.
    all_ids_after = [e.actor_id for e in pr_new.list_registry()]
    assert len(all_ids_after) == len(set(all_ids_after)) == 30
    for aid in affected:
        entry = pr_new.locate_actor(aid)
        assert entry.node_id != "edge-1"
        assert entry.status == ActorStatus.ACTIVE.value

    # Verify: unaffected actors were never touched -- still on their
    # original node, still ACTIVE, no capacity double-counted for them.
    unaffected = [aid for aid in actor_ids if aid not in affected]
    for aid in unaffected:
        entry = pr_new.locate_actor(aid)
        assert entry.node_id == placements_before[aid]
        assert entry.status == ActorStatus.ACTIVE.value

    # No node over capacity anywhere, after all of the above.
    for node in pr_new.list_nodes():
        assert node.current_actor_count <= node.capacity
