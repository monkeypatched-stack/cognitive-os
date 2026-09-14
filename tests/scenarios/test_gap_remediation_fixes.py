"""Gap Remediation audit — regression tests for the fixes applied against
the "CognitiveOS Actual-Code Architecture Audit" findings:

  1. multi-process reconcile-queue race (scheduled_elsewhere re-enqueue) -> test_01, test_02
  2. Scheduler -> Kubernetes gap (KubernetesProvisioner)                 -> test_03..test_07
  3. REDIS_URL / REDIS_HOST env-var convention split                    -> test_08, test_09
  4. MONGODB_URL / DATABASE_URL env-var convention split                -> test_10..test_13
  5. single-actor pod's backstop sweep scoping (scope_actor_id)         -> test_14, test_15
  6. _load_actors() cross-actor registry corruption (ACTOR_ID scoping)  -> test_16, test_17

Run with:
    python -m pytest tests/scenarios/test_gap_remediation_fixes.py -v
"""

from __future__ import annotations

import os
import subprocess

import pytest

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_RPS"] = "100000"
os.environ["RATE_LIMIT_BURST"] = "200000"

from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
)
from src.monkey_brain.kernel.society.actor_scheduler import ExecutionNode, NodeClass
from src.monkey_brain.kernel.society import kubernetes_provisioner as k8sprov
from src.monkey_brain.kernel.society.kubernetes_provisioner import KubernetesProvisioner
from src.monkey_brain.persistence.db_pool import DBPool
from tests.scenarios.test_horizontal_scheduler_scaling import _FakeRedis, _pr, _register

# ── 1-2: scheduled_elsewhere re-enqueues instead of dropping the signal ──


def test_01_scheduled_elsewhere_reenqueues_actor_id():
    redis = _FakeRedis()
    owner = _pr(redis, "owner-node")
    wrong = _pr(redis, "wrong-node")
    owner.register_node(ExecutionNode(node_id="owner-node", capacity=5))
    wrong.register_node(ExecutionNode(node_id="wrong-node", capacity=5))

    entry = _register(owner, "alice")
    aid = entry.actor_id
    decision = owner.scheduler.schedule(aid)
    assert decision.scheduled and decision.node_id == "owner-node"

    # Drain the registration-time enqueue so the queue is empty before
    # the assertion below.
    redis.lpop(wrong._RECONCILE_QUEUE_KEY, 10)

    result = wrong.lifecycle.reconcile(aid)
    assert result.action == "scheduled_elsewhere"

    queued = redis._lists.get(wrong._RECONCILE_QUEUE_KEY, [])
    assert aid in queued  # re-enqueued, not silently dropped


def test_02_scheduled_elsewhere_reenqueue_lets_the_correct_node_eventually_converge():
    redis = _FakeRedis()
    owner = _pr(redis, "owner-node")
    wrong = _pr(redis, "wrong-node")
    owner.register_node(ExecutionNode(node_id="owner-node", capacity=5))
    wrong.register_node(ExecutionNode(node_id="wrong-node", capacity=5))

    entry = _register(owner, "bob")
    aid = entry.actor_id
    owner.scheduler.schedule(aid)

    # Simulate the wrong process draining the queue entry first.
    redis.lpop(wrong._RECONCILE_QUEUE_KEY, 10)
    wrong.lifecycle.reconcile(aid)  # scheduled_elsewhere, re-enqueues

    # The correct owner now drains the re-enqueued entry and converges.
    batch = owner._drain_reconcile_queue_batch(10)
    assert aid in batch
    result = owner.lifecycle.reconcile(aid)
    assert result.action in ("start", "resume", "none")
    assert result.succeeded


# ── 3-7: KubernetesProvisioner ────────────────────────────────────────────


def test_03_provisioning_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KUBERNETES_PROVISIONING_ENABLED", raising=False)
    assert k8sprov.provisioning_enabled() is False


def test_04_provisioning_enabled_via_env(monkeypatch):
    monkeypatch.setenv("KUBERNETES_PROVISIONING_ENABLED", "true")
    assert k8sprov.provisioning_enabled() is True


def test_05_should_provision_only_for_no_healthy_nodes_registered():
    assert k8sprov.should_provision("no healthy nodes registered") is True
    assert k8sprov.should_provision("no node satisfies required capability X") is False
    assert k8sprov.should_provision("all nodes at capacity") is False


def test_06_provision_returns_false_when_kubectl_missing(monkeypatch):
    monkeypatch.setattr(k8sprov.shutil, "which", lambda _: None)
    provisioner = KubernetesProvisioner(planetary=object())
    assert provisioner.provision("actor-1") is False


def test_07_provision_applies_rendered_template_via_kubectl(monkeypatch, tmp_path):
    template = tmp_path / "actor-deployment.yaml"
    template.write_text(
        "metadata:\n  name: cognitiveos-actor-${ACTOR_ID}\n"
        '  labels:\n    node-class: "${ACTOR_NODE_CLASS}"\n'
        '  version: "${ACTOR_ARTIFACT_VERSION}"\n'
    )
    monkeypatch.setenv("ACTOR_DEPLOYMENT_TEMPLATE_PATH", str(template))
    monkeypatch.setattr(k8sprov.shutil, "which", lambda _: "/usr/bin/kubectl")

    captured = {}

    def _fake_run(cmd, input, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["input"] = input
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="deployment.apps/cognitiveos-actor-alice configured",
            stderr="",
        )

    monkeypatch.setattr(k8sprov.subprocess, "run", _fake_run)

    class _FakePlanetary:
        _artifact_version = "2.3.4"

    provisioner = KubernetesProvisioner(planetary=_FakePlanetary())
    assert provisioner.provision("alice", node_class="edge") is True
    assert "cognitiveos-actor-alice" in captured["input"]
    assert "edge" in captured["input"]
    assert "2.3.4" in captured["input"]
    assert captured["cmd"][:3] == ["kubectl", "apply", "-n"]


def test_07b_provision_never_raises_on_kubectl_failure(monkeypatch, tmp_path):
    template = tmp_path / "actor-deployment.yaml"
    template.write_text("metadata:\n  name: cognitiveos-actor-${ACTOR_ID}\n")
    monkeypatch.setenv("ACTOR_DEPLOYMENT_TEMPLATE_PATH", str(template))
    monkeypatch.setattr(k8sprov.shutil, "which", lambda _: "/usr/bin/kubectl")

    def _fake_run(*a, **kw):
        return subprocess.CompletedProcess([], 1, stdout="", stderr="apply rejected")

    monkeypatch.setattr(k8sprov.subprocess, "run", _fake_run)
    provisioner = KubernetesProvisioner(planetary=object())
    assert provisioner.provision("actor-x") is False


# ── 8-9: REDIS_URL / REDIS_HOST convention split ──────────────────────────


def test_08_init_persistence_prefers_explicit_redis_host(monkeypatch):
    monkeypatch.setenv("REDIS_HOST", "explicit-host")
    monkeypatch.setenv("REDIS_URL", "redis://url-host:6379/0")
    calls = {}

    class _FakeRedisClient:
        def __init__(self, **kwargs):
            calls["ctor"] = kwargs

        def ping(self):
            return True

        @classmethod
        def from_url(cls, *a, **kw):
            calls["from_url"] = (a, kw)
            return cls()

    # _init_persistence does `import redis as _redis` locally, which
    # resolves the real, already-imported `redis` module from
    # sys.modules -- patching its Redis attribute is what the function
    # actually sees.
    import redis as real_redis

    monkeypatch.setattr(real_redis, "Redis", _FakeRedisClient)
    pr = PlanetaryRuntime.__new__(PlanetaryRuntime)
    pr._init_persistence()
    assert "from_url" not in calls
    assert calls["ctor"]["host"] == "explicit-host"


def test_09_init_persistence_falls_back_to_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://url-only-host:6380/2")
    calls = {}

    class _FakeRedisClient:
        def ping(self):
            return True

        @classmethod
        def from_url(cls, url, **kw):
            calls["url"] = url
            return cls()

    import redis as real_redis

    monkeypatch.setattr(real_redis, "Redis", _FakeRedisClient)
    pr = PlanetaryRuntime.__new__(PlanetaryRuntime)
    pr._init_persistence()
    assert calls["url"] == "redis://url-only-host:6380/2"


# ── 10-13: MONGODB_URL / DATABASE_URL convention split ────────────────────


def test_10_dbpool_prefers_explicit_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mongodb://explicit/cognitive_platform")
    monkeypatch.setenv("MONGODB_URL", "mongodb://other:27017")
    pool = DBPool()
    assert pool.connection_string == "mongodb://explicit/cognitive_platform"


def test_11_dbpool_falls_back_to_mongodb_url_with_appended_db_name(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    monkeypatch.setenv("MONGODB_URL", "mongodb://mongodb:27017")
    pool = DBPool()
    assert pool.connection_string == "mongodb://mongodb:27017/cognitive_platform"


def test_12_dbpool_mongodb_url_fallback_honors_db_name_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MONGODB_URL", "mongodb://mongodb:27017")
    monkeypatch.setenv("DB_NAME", "tenant_alpha")
    pool = DBPool()
    assert pool.connection_string == "mongodb://mongodb:27017/tenant_alpha"


def test_13_dbpool_mongodb_url_with_own_db_path_is_left_untouched(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MONGODB_URL", "mongodb://mongodb:27017/already_scoped")
    pool = DBPool()
    assert pool.connection_string == "mongodb://mongodb:27017/already_scoped"


def test_13b_dbpool_default_unchanged_when_neither_env_var_set(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MONGODB_URL", raising=False)
    pool = DBPool()
    assert pool.connection_string == "mongodb://localhost:27017/cognitive_platform"


# ── 14-15: single-actor backstop sweep scoping ────────────────────────────


def test_14_scope_actor_id_defaults_to_none_full_sweep():
    redis = _FakeRedis()
    pr = _pr(redis)
    assert pr._reconciliation_scope_actor_id is None


def test_15_start_reconciliation_records_scope_actor_id(monkeypatch):
    redis = _FakeRedis()
    pr = _pr(redis)
    monkeypatch.setenv("SCHEDULER_SELF_REGISTER", "false")

    import asyncio

    async def _run():
        pr.start_actor_lifecycle_reconciliation(scope_actor_id="alice")
        assert pr._reconciliation_scope_actor_id == "alice"
        await pr.stop_actor_lifecycle_reconciliation()

    asyncio.run(_run())


# ── 16-17: _load_actors() cross-actor registry corruption fix ────────────


def test_16_load_actors_scoped_to_actor_id_env_var(monkeypatch):
    """Live Deployment Validation finding (P0): a single-actor pod
    previously loaded and locally activated EVERY actor in the shared
    registry, not just its own -- confirmed live to cause
    suspend_actor_for_migration to stamp a wrong-owner pod's own
    node_id into another actor's durable registry record during a
    concurrent multi-actor rollout. _load_actors() must only register
    the actor matching ACTOR_ID when that env var is set."""
    redis = _FakeRedis()
    redis._hashes["monkeybrain:actors:hash"] = {
        "alice-id": '{"identity": {"actor_id": "alice-id", "name": "alice", "actor_type": "ai_agent", "description": ""}, "capabilities": [], "goals": [], "policies": [], "trust_level": 0.5, "ownership": "", "objective": "", "metadata": {}, "society_id": "", "status": "active"}',
        "bob-id": '{"identity": {"actor_id": "bob-id", "name": "bob", "actor_type": "ai_agent", "description": ""}, "capabilities": [], "goals": [], "policies": [], "trust_level": 0.5, "ownership": "", "objective": "", "metadata": {}, "society_id": "", "status": "active"}',
    }
    monkeypatch.setenv("ACTOR_ID", "alice-id")
    pr = _pr(redis)
    pr._load_actors()

    all_ids = [s.actor_id for sr in pr.all_societies() for s in sr.all_actors()]
    assert "alice-id" in all_ids
    assert "bob-id" not in all_ids


def test_17_load_actors_unscoped_when_actor_id_unset(monkeypatch):
    """Every existing multi-actor caller (deployment.yaml's control-plane
    pod, tests) never sets ACTOR_ID -- must see the exact prior
    full-load behavior, unaffected by this fix."""
    redis = _FakeRedis()
    redis._hashes["monkeybrain:actors:hash"] = {
        "alice-id": '{"identity": {"actor_id": "alice-id", "name": "alice", "actor_type": "ai_agent", "description": ""}, "capabilities": [], "goals": [], "policies": [], "trust_level": 0.5, "ownership": "", "objective": "", "metadata": {}, "society_id": "", "status": "active"}',
        "bob-id": '{"identity": {"actor_id": "bob-id", "name": "bob", "actor_type": "ai_agent", "description": ""}, "capabilities": [], "goals": [], "policies": [], "trust_level": 0.5, "ownership": "", "objective": "", "metadata": {}, "society_id": "", "status": "active"}',
    }
    monkeypatch.delenv("ACTOR_ID", raising=False)
    pr = _pr(redis)
    pr._load_actors()

    all_ids = [s.actor_id for sr in pr.all_societies() for s in sr.all_actors()]
    assert "alice-id" in all_ids
    assert "bob-id" in all_ids


# ── 18-19: Redis client resilience + observability (Failure 9) ───────────


def test_18_init_persistence_configures_retry_on_connection_errors(monkeypatch):
    """Live Deployment Validation finding: a real, long-running control-plane
    process silently stopped persisting new actor registrations to Redis
    after a period of otherwise-normal operation, while a fresh process
    against the identical server/code worked on the first try -- the
    likely cause is a connection that went stale in the pool with no
    retry configured, so the first command to hit it failed once instead
    of transparently reconnecting. _init_persistence() must now construct
    its Redis client with retry_on_error configured (covering both
    ConnectionError and TimeoutError)."""
    import redis as real_redis

    captured = {}

    class _FakeRedisClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def ping(self):
            return True

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_HOST", "some-host")
    monkeypatch.setattr(real_redis, "Redis", _FakeRedisClient)
    pr = PlanetaryRuntime.__new__(PlanetaryRuntime)
    pr._init_persistence()

    retry_errors = captured.get("retry_on_error", [])
    assert real_redis.exceptions.ConnectionError in retry_errors
    assert real_redis.exceptions.TimeoutError in retry_errors
    assert captured.get("socket_timeout")
    assert captured.get("health_check_interval")


def test_19_save_actor_failure_is_logged_at_warning_not_debug(monkeypatch, caplog):
    """The prior DEBUG-level swallow was invisible at this deployment's
    default LOG_LEVEL=INFO -- a durable-persistence failure (actor stays
    registered in-memory, caller sees success, Registry write silently
    never happens) must be visible without an operator already knowing
    to enable DEBUG logging to find it."""
    import logging

    redis = _FakeRedis()
    pr = _pr(redis)
    entry = _register(pr, "carol")

    def _raise(*a, **kw):
        raise ConnectionError("simulated stale connection")

    monkeypatch.setattr(pr._redis, "hset", _raise)
    with caplog.at_level(logging.WARNING, logger="agentos.planetary_runtime"):
        pr._save_actor(pr._society_runtime.get_actor(entry.actor_id))
    assert any("Actor save failed" in r.message for r in caplog.records)


# ── 20: migrate_away re-enqueues (Phase 9 live-discovered gap) ───────────


def test_20_migrate_away_reenqueues_actor_id():
    """Live Deployment Validation finding: an actor suspended via
    _do_migrate_away (e.g. the control-plane's own backstop sweep
    discovering an actor it's resident on should really live elsewhere)
    sat suspended for 8+ minutes past its target node's 300s backstop
    interval in a real live test, because nothing woke the target
    node's fast queue-drain path -- only _consult_scheduler's sibling
    branches (scheduled_elsewhere/UNSCHEDULABLE) re-enqueued after a
    state change. _do_migrate_away must do the same."""
    redis = _FakeRedis()
    pr = _pr(redis, "this-node")
    entry = _register(pr, "dave")
    aid = entry.actor_id
    result = pr.lifecycle.reconcile(aid)
    assert result.action == "start"

    # Simulate the scheduler now placing this actor elsewhere.
    pr.set_actor_desired_node(aid, "other-node")
    redis.lpop(pr._RECONCILE_QUEUE_KEY, 10)  # drain any prior enqueue

    result = pr.lifecycle.reconcile(aid)
    assert result.action == "migrate_away"
    assert result.succeeded

    queued = redis._lists.get(pr._RECONCILE_QUEUE_KEY, [])
    assert aid in queued  # re-enqueued, not silently dropped


# ── 21: node registration drift on reconciliation startup ────────────────


def test_21_reconciliation_startup_preserves_existing_node_registration():
    """Live Edge Deployment Validation finding (P1): start_actor_
    lifecycle_reconciliation()'s own internal register_self_as_node()
    call previously always passed zero args, silently overwriting
    whatever node_class/capacity a caller had already correctly
    registered moments earlier with the generic SCHEDULER_NODE_CLASS/
    _CAPACITY env-var defaults (cloud/1000) -- confirmed live: an edge
    actor's correct node_class=EDGE registration flipped back to
    "cloud" on this exact call, breaking every subsequent
    required_node_class=edge placement. Must preserve an
    already-existing registration for this node_id instead."""
    redis = _FakeRedis()
    pr = _pr(redis, "edge-node-1")
    pr.register_self_as_node(node_class=NodeClass.EDGE, capacity=1)
    assert pr.get_node("edge-node-1").node_class == NodeClass.EDGE

    import asyncio

    async def _run():
        pr.start_actor_lifecycle_reconciliation()
        await pr.stop_actor_lifecycle_reconciliation()

    asyncio.run(_run())

    node = pr.get_node("edge-node-1")
    assert node.node_class == NodeClass.EDGE
    assert node.capacity == 1


def test_21b_reconciliation_startup_falls_back_for_a_genuinely_new_node():
    """A node_id with no prior registration at all still falls back to
    the documented SCHEDULER_NODE_CLASS/_CAPACITY env-var defaults --
    unaffected by the fix above."""
    redis = _FakeRedis()
    pr = _pr(redis, "brand-new-node")

    import asyncio

    async def _run():
        pr.start_actor_lifecycle_reconciliation()
        await pr.stop_actor_lifecycle_reconciliation()

    asyncio.run(_run())

    node = pr.get_node("brand-new-node")
    assert node is not None
    assert node.node_class == NodeClass.CLOUD


# ── 22: scheduler self-capacity double-counting ───────────────────────────


def test_22_schedule_idempotent_shortcut_excludes_self_from_capacity(monkeypatch):
    """Live Edge Deployment Validation finding (P1): a genuine
    one-actor-per-node registration (capacity=1, the documented
    Kubernetes/edge convention) always has current_actor_count=1 once
    the actor itself has registered -- making schedule()'s own "am I
    already validly placed here" shortcut self-defeating (available_
    capacity is permanently 0 against itself), forcing every reconcile
    to fall through to fresh candidate search and pick a DIFFERENT,
    more-available node instead of confirming the actor's own explicit
    self-claim. Confirmed live: an edge actor with capacity=1 flip-
    flopped onto a shared, higher-capacity node instead of staying on
    its own dedicated process."""
    redis = _FakeRedis()
    pr = _pr(redis, "actor-own-node")
    pr.register_node(ExecutionNode(node_id="actor-own-node", capacity=1, current_actor_count=1))
    entry = _register(pr, "frank")
    aid = entry.actor_id
    pr.scheduler.migrate_actor(aid, target_node_id="actor-own-node")

    decision = pr.scheduler.schedule(aid)
    assert decision.scheduled is True
    assert decision.node_id == "actor-own-node"
    assert "already validly placed" in decision.reason


def test_22b_fresh_candidate_search_still_counts_every_resident_actor(monkeypatch):
    """The fix above must NOT relax capacity checks for a genuinely
    fresh candidate search (a DIFFERENT actor asking to move onto an
    already-full node) -- only the idempotent "am I still valid where I
    already am" shortcut excludes self."""
    redis = _FakeRedis()
    pr = _pr(redis, "full-node")
    pr.register_node(ExecutionNode(node_id="full-node", capacity=1, current_actor_count=1))
    entry = _register(pr, "grace")
    aid = entry.actor_id
    # grace has never been placed anywhere -- a genuinely fresh schedule()
    # call against a node that's already full with a DIFFERENT actor.
    decision = pr.scheduler.schedule(aid)
    assert decision.scheduled is False
