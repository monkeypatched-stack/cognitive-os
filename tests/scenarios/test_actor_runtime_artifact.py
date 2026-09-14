"""Cloud/Edge Actor Convergence + Actor-as-Deployable-Binary — qualification
tests.

Covers, combined (the two tasks share almost entirely the same underlying
mechanism -- one Actor abstraction, placed via node_class, hosted by one
Actor Runtime):

  Cloud/Edge Actor Convergence:
    real CognitiveActor on EDGE node class      -> test_01
    real CognitiveActor on CLOUD node class      -> test_02
    migration CLOUD -> EDGE, identity preserved  -> test_03
    node failure EDGE -> recovers on CLOUD        -> test_04
    actor-to-actor across node classes            -> test_05
    offline safety: SAFE_OFFLINE always allowed   -> test_06
    offline safety: REQUIRES_AUTHORITY blocked
      when DISCONNECTED, allowed when CONNECTED   -> test_07, test_08

  Actor as Deployable Binary (Actor Artifact):
    config: env vars only                         -> test_09
    config: file only (YAML)                       -> test_10
    config: env overrides file                     -> test_11
    config: CLI override (--actor-id)               -> test_12
    missing ACTOR_ID raises clearly                -> test_13
    identity: must pre-exist unless bootstrap       -> test_14
    bootstrap creates a new actor when requested    -> test_15
    startup reaches READY for a pre-registered actor -> test_16
    two Actor instances from the "same binary"      -> test_17
    restart: new process, same actor_id, no dup     -> test_18
    artifact metadata surfaces on the registry entry -> test_19
    graceful shutdown checkpoints, never deletes     -> test_20
    claim_placement explicitly claims this node      -> test_21
    SCHEDULED_ELSEWHERE when claimed elsewhere       -> test_22
    status()/artifact_info() shape                   -> test_23

Per this repo's session convention, this file is written but not executed
by the assistant. Run with:
    python -m pytest tests/scenarios/test_actor_runtime_artifact.py -v
"""

from __future__ import annotations

import json
import os
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
from src.monkey_brain.kernel.society.actor_scheduler import ExecutionNode, NodeClass
from src.monkey_brain.kernel.pipeline.offline_safety import (
    ConnectivityStatus,
    assess_connectivity,
    check_operation_allowed,
    classify_capability,
    OperationSafety,
)
from src.monkey_brain.actor_runtime import (
    ActorRuntime,
    ActorRuntimeConfig,
    ReadinessState,
)


class _FakeRedis:
    """Same minimal in-memory redis-py subset established across this
    session's other test files (actor/node registries, lease, capacity
    reservation EVAL, reconcile queue)."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expiry: dict[str, float] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._lists: dict[str, list[str]] = {}

    def _expired(self, key):
        exp = self._expiry.get(key)
        return exp is not None and time.time() > exp

    def ping(self):
        return True

    def set(self, key, value, nx=False, ex=None):
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

    def rpush(self, name, *values):
        self._lists.setdefault(name, []).extend(values)
        return len(self._lists[name])

    def lpop(self, name, count=None):
        lst = self._lists.get(name, [])
        if not lst:
            return None if count is None else []
        if count is None:
            return lst.pop(0)
        popped, self._lists[name] = lst[:count], lst[count:]
        return popped

    def eval(self, script, numkeys, key, *args):
        if "cjson" in script:
            node_id, delta, ts = args
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
        if self._store.get(key) == token and not self._expired(key):
            del self._store[key]
            return 1
        return 0


def _register(pr: PlanetaryRuntime, name: str, **kwargs):
    return pr.register_actor(
        ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.AI_AGENT)),
        **kwargs,
    )


async def _noop_nats(*_a, **_k):
    return False


@pytest.fixture(autouse=True)
def _restore_environ():
    """ActorRuntime.start() sets a few env vars directly (ACTOR_ARTIFACT_
    VERSION/ACTOR_RUNTIME_VERSION/COGNITIVEOS_NODE_ID/OFFLINE_SAFETY_GATE_
    ENABLED) as real process configuration, not via pytest's monkeypatch —
    correct for production (a real process sets these once, for its own
    lifetime), but each test in this file needs a clean slate rather than
    inheriting whatever a previous test's runtime.start() call left
    behind."""
    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)


def _pr(redis=None, node_id: str = "") -> PlanetaryRuntime:
    pr = PlanetaryRuntime()
    if redis is not None:
        pr._redis = redis
    if node_id:
        pr._node_id = node_id
    pr.connect_nats = _noop_nats  # avoid a real network attempt in tests
    return pr


# ── 1-2: Real CognitiveActor on EDGE / CLOUD node classes ────────────────


def test_01_real_actor_placed_and_started_on_edge_node_class():
    redis = _FakeRedis()
    pr = _pr(redis, "edge-node-1")
    pr.register_node(ExecutionNode(node_id="edge-node-1", node_class=NodeClass.EDGE, capacity=1))
    state = _register(pr, "edge_actor")
    result = pr.lifecycle.reconcile(state.actor_id)
    assert result.action == "start"
    assert result.succeeded is True
    sr = pr._home_society_runtime(state.actor_id)
    assert sr.get_actor(state.actor_id).status == ActorStatus.ACTIVE
    # This is the real, governed CognitiveActor -- not the standalone
    # EdgeActor prototype -- confirmed by real cognition attributes.
    actor = sr.get_actor(state.actor_id).actor
    assert hasattr(actor, "belief") and hasattr(actor, "policy") and hasattr(actor, "_affiliations")


def test_02_same_code_path_places_on_cloud_node_class():
    redis = _FakeRedis()
    pr = _pr(redis, "cloud-node-1")
    pr.register_node(ExecutionNode(node_id="cloud-node-1", node_class=NodeClass.CLOUD, capacity=10))
    state = _register(pr, "cloud_actor")
    result = pr.lifecycle.reconcile(state.actor_id)
    assert result.action == "start"
    node = pr.get_node("cloud-node-1")
    assert node.current_actor_count == 1
    assert node.node_class == NodeClass.CLOUD


# ── 3: Migration CLOUD -> EDGE ────────────────────────────────────────────


def test_03_migration_cloud_to_edge_preserves_identity():
    redis = _FakeRedis()
    pr_cloud = _pr(redis, "cloud-node-1")
    pr_edge = _pr(redis, "edge-node-1")
    pr_cloud.register_node(ExecutionNode(node_id="cloud-node-1", node_class=NodeClass.CLOUD, capacity=10))
    pr_cloud.register_node(ExecutionNode(node_id="edge-node-1", node_class=NodeClass.EDGE, capacity=10))

    state = _register(pr_cloud, "migrating_actor")
    aid = state.actor_id
    pr_cloud.set_actor_desired_node(aid, "cloud-node-1")
    pr_cloud._reserve_node_capacity("cloud-node-1", 1)
    assert pr_cloud.lifecycle.reconcile(aid).action == "start"

    migrate_decision = pr_cloud.scheduler.migrate_actor(aid, target_node_id="edge-node-1")
    assert migrate_decision.node_id == "edge-node-1"
    sr_cloud = pr_cloud._home_society_runtime(aid)
    assert sr_cloud.get_actor(aid).status == ActorStatus.SUSPENDED

    resume_result = pr_edge.lifecycle.reconcile(aid)
    assert resume_result.action == "resume"
    sr_edge = pr_edge._home_society_runtime(aid)
    assert sr_edge.get_actor(aid).status == ActorStatus.ACTIVE
    # SAME actor_id throughout -- no new identity was ever created.
    assert sr_edge.get_actor(aid).actor_id == aid
    assert pr_edge.locate_actor(aid).node_id == "edge-node-1"


# ── 4: Node failure EDGE -> recovers on CLOUD ─────────────────────────────


def test_04_edge_node_failure_recovers_actor_on_cloud():
    redis = _FakeRedis()
    pr = _pr(redis, "edge-node-1")
    pr.register_node(ExecutionNode(node_id="edge-node-1", node_class=NodeClass.EDGE, capacity=5))
    state = _register(pr, "edge_actor")
    aid = state.actor_id
    assert pr.lifecycle.reconcile(aid).action == "start"

    raw = json.loads(redis.hget(pr._ACTORS_HASH_KEY, aid))
    raw["updated_at"] = time.time() - (pr._ACTOR_STALE_SECONDS + 100)
    redis.hset(pr._ACTORS_HASH_KEY, aid, json.dumps(raw))

    pr_cloud = _pr(redis, "cloud-node-1")
    pr_cloud.deregister_node("edge-node-1")
    pr_cloud.register_node(ExecutionNode(node_id="cloud-node-1", node_class=NodeClass.CLOUD, capacity=5))

    assert pr_cloud.observe_actor(aid).is_stale is True
    recover_result = pr_cloud.lifecycle.reconcile(aid)
    assert recover_result.action == "recover"
    assert recover_result.actor_id == aid  # identity unchanged
    entry = pr_cloud.locate_actor(aid)
    assert entry.node_id == "cloud-node-1"
    assert entry.status == ActorStatus.ACTIVE.value
    # No duplicate identity.
    assert [e.actor_id for e in pr_cloud.list_registry()].count(aid) == 1


# ── 5: Actor-to-actor across node classes ─────────────────────────────────


def test_05_actor_to_actor_resolves_across_node_classes():
    """Alice (cloud) can resolve Bob (edge) purely via the Actor Registry
    -- confirms location-independent addressing already holds regardless
    of node_class, not just regardless of which process."""
    redis = _FakeRedis()
    pr_edge = _pr(redis, "edge-node-1")
    pr_edge.register_node(ExecutionNode(node_id="edge-node-1", node_class=NodeClass.EDGE, capacity=5))
    bob = pr_edge.register_actor(
        ActorProfile(identity=ActorIdentity(name="Bob", actor_type=ActorType.AI_AGENT)),
    )
    assert pr_edge.lifecycle.reconcile(bob.actor_id).action == "start"

    pr_cloud = _pr(redis, "cloud-node-1")
    entry = pr_cloud.locate_actor(bob.actor_id)
    assert entry is not None
    assert entry.name == "Bob"
    assert entry.node_id == "edge-node-1"


# ── 6-8: Offline safety classification ────────────────────────────────────


def test_06_safe_offline_capability_always_allowed():
    allowed, waiting_state, _ = check_operation_allowed("AnswerQuestionCapability", ConnectivityStatus.DISCONNECTED)
    assert allowed is True
    assert waiting_state == ""


def test_07_requires_authority_blocked_when_disconnected():
    allowed, waiting_state, reason = check_operation_allowed("PaymentCapability", ConnectivityStatus.DISCONNECTED)
    assert allowed is False
    assert waiting_state == "DISCONNECTED"
    assert "PaymentCapability" in reason


def test_08_requires_authority_allowed_when_connected():
    allowed, waiting_state, _ = check_operation_allowed("PaymentCapability", ConnectivityStatus.CONNECTED)
    assert allowed is True
    assert waiting_state == ""
    # And an unclassified, unknown capability defaults to the conservative
    # bucket (module docstring: "never assumed safe").
    assert classify_capability("SomeBrandNewCapabilityNeverSeenBefore") == OperationSafety.REQUIRES_AUTHORITY


# ── 9-13: Config loading ──────────────────────────────────────────────────


def test_09_config_from_env_vars_only(monkeypatch):
    monkeypatch.setenv("ACTOR_ID", "actor-from-env")
    monkeypatch.setenv("ACTOR_NODE_CLASS", "edge")
    monkeypatch.setenv("ACTOR_NODE_CAPABILITIES", "camera, gripper")
    config = ActorRuntimeConfig.load()
    assert config.actor_id == "actor-from-env"
    assert config.node_class == "edge"
    assert config.node_capabilities == ("camera", "gripper")


def test_10_config_from_file_only(tmp_path, monkeypatch):
    monkeypatch.delenv("ACTOR_ID", raising=False)
    config_file = tmp_path / "actor.yaml"
    config_file.write_text("actor_id: actor-from-file\nnode_class: device\nnode_capacity: 3\n")
    config = ActorRuntimeConfig.load(str(config_file))
    assert config.actor_id == "actor-from-file"
    assert config.node_class == "device"
    assert config.node_capacity == 3


def test_11_env_var_overrides_config_file(tmp_path, monkeypatch):
    config_file = tmp_path / "actor.yaml"
    config_file.write_text("actor_id: actor-from-file\nnode_class: device\n")
    monkeypatch.setenv("ACTOR_ID", "actor-from-env-wins")
    config = ActorRuntimeConfig.load(str(config_file))
    assert config.actor_id == "actor-from-env-wins"
    assert config.node_class == "device"  # still comes from the file


def test_12_cli_override_wins_over_everything(monkeypatch):
    monkeypatch.setenv("ACTOR_ID", "actor-from-env")
    config = ActorRuntimeConfig.load(overrides={"actor_id": "actor-from-cli"})
    assert config.actor_id == "actor-from-cli"


def test_13_missing_actor_id_raises_clearly(monkeypatch):
    monkeypatch.delenv("ACTOR_ID", raising=False)
    with pytest.raises(ValueError, match="ACTOR_ID"):
        ActorRuntimeConfig.load()


# ── 14-15: Identity establishment ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_14_actor_must_preexist_unless_bootstrap():
    redis = _FakeRedis()
    pr = _pr(redis, "node-1")
    pr.register_node(ExecutionNode(node_id="node-1", capacity=5))
    config = ActorRuntimeConfig(actor_id="never-registered", bootstrap_if_missing=False)
    runtime = ActorRuntime(config, planetary_runtime_factory=lambda: pr)
    await runtime.start()
    assert runtime.state == ReadinessState.NOT_FOUND
    assert "never-registered" in runtime.state_reason


@pytest.mark.asyncio
async def test_15_bootstrap_creates_actor_when_requested():
    redis = _FakeRedis()
    pr = _pr(redis, "node-1")
    pr.register_node(ExecutionNode(node_id="node-1", capacity=5))
    config = ActorRuntimeConfig(actor_id="bootstrap-me", bootstrap_if_missing=True, node_id="node-1")
    runtime = ActorRuntime(config, planetary_runtime_factory=lambda: pr)
    await runtime.start()
    assert runtime.state == ReadinessState.READY
    assert pr.locate_actor("bootstrap-me") is not None
    await runtime.shutdown()


# ── 16: Startup reaches READY ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_16_startup_reaches_ready_for_preregistered_actor():
    redis = _FakeRedis()
    pr_setup = _pr(redis)
    preregistered = _register(pr_setup, "preregistered_actor").actor_id

    pr = _pr(redis, "edge-node-1")
    pr.register_node(ExecutionNode(node_id="edge-node-1", node_class=NodeClass.EDGE, capacity=1))
    config = ActorRuntimeConfig(
        actor_id=preregistered,
        node_id="edge-node-1",
        node_class="edge",
        claim_placement=True,
    )
    runtime = ActorRuntime(config, planetary_runtime_factory=lambda: pr)
    await runtime.start()
    assert runtime.state == ReadinessState.READY
    assert runtime.ready_since is not None
    status = runtime.status()
    assert status["ready"] is True
    assert status["observed"]["resident_here"] is True
    await runtime.shutdown()


# ── 17: Two Actor instances from the "same binary" ────────────────────────


@pytest.mark.asyncio
async def test_17_two_actor_instances_run_independently():
    redis = _FakeRedis()
    pr_setup = _pr(redis)
    a_id = _register(pr_setup, "actor_a").actor_id
    b_id = _register(pr_setup, "actor_b").actor_id

    pr_a = _pr(redis, "node-a")
    pr_a.register_node(ExecutionNode(node_id="node-a", capacity=1))
    pr_b = _pr(redis, "node-b")
    pr_b.register_node(ExecutionNode(node_id="node-b", capacity=1))

    # Same ActorRuntime/ActorRuntimeConfig classes -- the reusable
    # "binary" -- instantiated twice with different actor_id/config,
    # proving the artifact is not actor-specific.
    runtime_a = ActorRuntime(
        ActorRuntimeConfig(actor_id=a_id, node_id="node-a", claim_placement=True),
        planetary_runtime_factory=lambda: pr_a,
    )
    runtime_b = ActorRuntime(
        ActorRuntimeConfig(actor_id=b_id, node_id="node-b", claim_placement=True),
        planetary_runtime_factory=lambda: pr_b,
    )
    await runtime_a.start()
    await runtime_b.start()

    assert runtime_a.state == ReadinessState.READY
    assert runtime_b.state == ReadinessState.READY
    assert pr_a.locate_actor(a_id).node_id == "node-a"
    assert pr_b.locate_actor(b_id).node_id == "node-b"
    # Failure/state of one instance's config object is fully independent.
    assert runtime_a.config.actor_id != runtime_b.config.actor_id

    await runtime_a.shutdown()
    await runtime_b.shutdown()


# ── 18: Restart -- new process, same identity, no duplication ────────────


@pytest.mark.asyncio
async def test_18_restart_same_actor_id_no_duplicate():
    redis = _FakeRedis()
    pr_setup = _pr(redis)
    aid = _register(pr_setup, "restart_actor").actor_id

    pr_1 = _pr(redis, "node-1")
    pr_1.register_node(ExecutionNode(node_id="node-1", capacity=5))
    runtime_1 = ActorRuntime(
        ActorRuntimeConfig(actor_id=aid, node_id="node-1", claim_placement=True),
        planetary_runtime_factory=lambda: pr_1,
    )
    await runtime_1.start()
    assert runtime_1.state == ReadinessState.READY
    await runtime_1.shutdown()  # "process killed" -- graceful path

    # "Same Actor binary starts again" -- a brand-new ActorRuntime/
    # PlanetaryRuntime, same actor_id, same shared registry.
    pr_2 = _pr(redis, "node-1")
    runtime_2 = ActorRuntime(
        ActorRuntimeConfig(actor_id=aid, node_id="node-1", claim_placement=True),
        planetary_runtime_factory=lambda: pr_2,
    )
    await runtime_2.start()
    assert runtime_2.state == ReadinessState.READY

    all_ids = [e.actor_id for e in pr_2.list_registry()]
    assert all_ids.count(aid) == 1  # no duplicate identity
    await runtime_2.shutdown()


# ── 19: Artifact metadata surfaces on the registry entry ──────────────────


@pytest.mark.asyncio
async def test_19_artifact_metadata_recorded_on_registry_entry():
    redis = _FakeRedis()
    pr_setup = _pr(redis)
    aid = _register(pr_setup, "versioned_actor").actor_id

    pr = _pr(redis, "node-1")
    pr.register_node(ExecutionNode(node_id="node-1", capacity=5))
    config = ActorRuntimeConfig(actor_id=aid, node_id="node-1", artifact_version="1.4", claim_placement=True)
    runtime = ActorRuntime(config, planetary_runtime_factory=lambda: pr)
    await runtime.start()

    entry = pr.locate_actor(aid)
    assert entry.artifact_version == "1.4"
    assert entry.runtime_version  # non-empty, defaults to ACTOR_RUNTIME_VERSION
    info = runtime.artifact_info()
    assert info["artifact_version"] == "1.4"
    assert info["actor_id"] == aid
    await runtime.shutdown()


# ── 20: Graceful shutdown checkpoints, never deletes the Actor ───────────


@pytest.mark.asyncio
async def test_20_shutdown_checkpoints_and_deregisters_but_never_deletes():
    redis = _FakeRedis()
    pr_setup = _pr(redis)
    aid = _register(pr_setup, "shutdown_actor").actor_id

    pr = _pr(redis, "node-1")
    pr.register_node(ExecutionNode(node_id="node-1", capacity=5))
    runtime = ActorRuntime(
        ActorRuntimeConfig(actor_id=aid, node_id="node-1", claim_placement=True),
        planetary_runtime_factory=lambda: pr,
    )
    await runtime.start()
    assert runtime.state == ReadinessState.READY

    await runtime.shutdown()

    # The Actor's identity/registry record STILL exists -- shutdown is not
    # deletion (Section 10).
    assert pr.locate_actor(aid) is not None
    # This node was deregistered -- a fresh scheduling decision would no
    # longer offer it.
    assert pr.get_node("node-1") is None


# ── 21-22: claim_placement / SCHEDULED_ELSEWHERE ──────────────────────────


@pytest.mark.asyncio
async def test_21_claim_placement_explicitly_claims_this_node():
    redis = _FakeRedis()
    pr_setup = _pr(redis)
    aid = _register(pr_setup, "claimed_actor").actor_id

    pr = _pr(redis, "specific-node")
    pr.register_node(ExecutionNode(node_id="other-node", capacity=5))  # a higher-ranked decoy
    pr.register_node(ExecutionNode(node_id="specific-node", capacity=5))
    runtime = ActorRuntime(
        ActorRuntimeConfig(actor_id=aid, node_id="specific-node", claim_placement=True),
        planetary_runtime_factory=lambda: pr,
    )
    await runtime.start()
    assert runtime.state == ReadinessState.READY
    assert pr.locate_actor(aid).node_id == "specific-node"
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_22_scheduled_elsewhere_when_another_node_already_claimed():
    redis = _FakeRedis()
    pr_setup = _pr(redis, "node-a")
    pr_setup.register_node(ExecutionNode(node_id="node-a", capacity=5))
    aid = _register(pr_setup, "elsewhere_actor").actor_id
    pr_setup.set_actor_desired_node(aid, "node-a")
    pr_setup._reserve_node_capacity("node-a", 1)

    pr_b = _pr(redis, "node-b")
    pr_b.register_node(ExecutionNode(node_id="node-b", capacity=5))
    runtime_b = ActorRuntime(
        ActorRuntimeConfig(actor_id=aid, node_id="node-b", claim_placement=False),
        planetary_runtime_factory=lambda: pr_b,
    )
    await runtime_b.start()
    assert runtime_b.state == ReadinessState.SCHEDULED_ELSEWHERE
    status = runtime_b.status()
    assert status["ready"] is False


# ── 23: status()/artifact_info() shape ─────────────────────────────────────


@pytest.mark.asyncio
async def test_23_status_and_artifact_info_shape():
    redis = _FakeRedis()
    pr_setup = _pr(redis)
    aid = _register(pr_setup, "shape_actor").actor_id

    pr = _pr(redis, "node-1")
    pr.register_node(ExecutionNode(node_id="node-1", capacity=5))
    runtime = ActorRuntime(
        ActorRuntimeConfig(actor_id=aid, node_id="node-1", artifact_version="2.0", claim_placement=True),
        planetary_runtime_factory=lambda: pr,
    )
    await runtime.start()

    status = runtime.status()
    for key in (
        "state",
        "reason",
        "ready",
        "ready_since",
        "observed",
        "actor_id",
        "artifact_version",
        "runtime_version",
        "node_id",
        "node_class",
        "started_at",
    ):
        assert key in status
    info = runtime.artifact_info()
    for key in (
        "actor_id",
        "artifact_version",
        "runtime_version",
        "node_id",
        "node_class",
        "started_at",
    ):
        assert key in info
    await runtime.shutdown()
