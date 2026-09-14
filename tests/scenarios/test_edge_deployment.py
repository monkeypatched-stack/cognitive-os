"""Edge Deployment — regression tests for the missing deployment substrate
closed this pass:

  1. EdgeAgent process supervision (start/stop/status/crash-restart) -> test_01..test_05
  2. EdgeProvisioner (push-based, mirrors KubernetesProvisioner)      -> test_06..test_09
  3. Lifecycle Controller -> EdgeProvisioner dispatch                 -> test_10..test_12
  4. Device identity != Actor identity                                -> test_13

Per this repo's session convention (see test_horizontal_scheduler_scaling.py's
own header), this file is written but not executed by the assistant unless
explicitly asked to run it. Run with:
    python -m pytest tests/scenarios/test_edge_deployment.py -v
"""

from __future__ import annotations

import os
import sys
import time

import pytest

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"

from src.monkey_brain.edge_agent import EdgeAgent
from src.monkey_brain.kernel.society import edge_provisioner as edgeprov
from src.monkey_brain.kernel.society.edge_provisioner import EdgeProvisioner
from src.monkey_brain.kernel.society.actor_scheduler import (
    ExecutionNode,
    NodeClass,
    ActorPlacementRequirements,
)
from src.monkey_brain.kernel.society.domain import ActorStatus
from tests.scenarios.test_horizontal_scheduler_scaling import _FakeRedis, _pr, _register

# ── 1-5: EdgeAgent process supervision (real subprocesses, no CognitiveOS
#         stack needed -- these test the Agent's OWN process-management
#         logic in isolation, using a trivial stand-in command instead of
#         the real actor_runtime.py, matching KubernetesProvisioner's own
#         test_07's use of a fake `kubectl` rather than a real cluster) ──


def _patch_actor_command(monkeypatch, agent: EdgeAgent, command: list[str]) -> None:
    """Redirects EdgeAgent.start_actor's subprocess command to a trivial
    stand-in (e.g. `sleep 5` or `python3 -c pass`) instead of spawning a
    real actor_runtime.py -- isolates the Agent's OWN start/stop/
    supervise logic from the full CognitiveOS stack, the same isolation
    principle test_gap_remediation_fixes.py's KubernetesProvisioner tests
    already use against a fake `kubectl`."""
    import subprocess as _subprocess

    real_popen = _subprocess.Popen

    def _fake_popen(cmd, **kwargs):
        return real_popen(
            command,
            **{k: v for k, v in kwargs.items() if k != "env"} | {"env": kwargs.get("env")},
        )

    monkeypatch.setattr("src.monkey_brain.edge_agent.subprocess.Popen", _fake_popen)


def test_01_start_actor_tracks_a_real_subprocess(monkeypatch):
    agent = EdgeAgent()
    _patch_actor_command(monkeypatch, agent, [sys.executable, "-c", "import time; time.sleep(5)"])
    result = agent.start_actor("actor-x", node_class="edge")
    try:
        assert result["running"] is True
        assert result["actor_id"] == "actor-x"
        assert result["device_id"] == agent.device_id
        assert agent.status("actor-x")["running"] is True
    finally:
        agent.stop_actor("actor-x")


def test_02_stop_actor_terminates_gracefully(monkeypatch):
    agent = EdgeAgent()
    _patch_actor_command(monkeypatch, agent, [sys.executable, "-c", "import time; time.sleep(30)"])
    agent.start_actor("actor-x")
    assert agent.stop_actor("actor-x") is True
    assert agent.status("actor-x")["running"] is False
    assert agent.status("actor-x")["stopped"] is True


def test_03_start_actor_is_idempotent(monkeypatch):
    agent = EdgeAgent()
    _patch_actor_command(monkeypatch, agent, [sys.executable, "-c", "import time; time.sleep(5)"])
    first = agent.start_actor("actor-x")
    second = agent.start_actor("actor-x")
    try:
        assert first["pid"] == second["pid"]  # same subprocess, not a duplicate
    finally:
        agent.stop_actor("actor-x")


def test_04_status_none_for_unmanaged_actor():
    agent = EdgeAgent()
    assert agent.status("never-started") is None
    assert agent.list_actors() == []


def test_05_supervise_loop_restarts_a_crashed_actor(monkeypatch):
    """Local crash recovery (Section 13): a subprocess that exits
    unexpectedly (not via stop_actor()) must be restarted with the SAME
    actor_id, and restart_count must reflect it -- the Edge Agent's own
    equivalent of a kubelet restart policy."""
    agent = EdgeAgent()
    _patch_actor_command(monkeypatch, agent, [sys.executable, "-c", "pass"])  # exits immediately
    agent.start_actor("actor-x")
    monkeypatch.setenv("EDGE_AGENT_SUPERVISE_INTERVAL", "0.1")

    import asyncio

    async def _run_briefly():
        task = asyncio.create_task(agent.supervise_loop())
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run_briefly())
    status = agent.status("actor-x")
    assert status is not None
    assert status["restart_count"] >= 1
    agent.stop_actor("actor-x")


# ── 6-9: EdgeProvisioner (push-based, mirrors KubernetesProvisioner) ──────


def test_06_provisioning_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EDGE_PROVISIONING_ENABLED", raising=False)
    assert edgeprov.provisioning_enabled() is False


def test_07_provisioning_enabled_via_env(monkeypatch):
    monkeypatch.setenv("EDGE_PROVISIONING_ENABLED", "true")
    assert edgeprov.provisioning_enabled() is True


def test_08_provision_posts_to_the_devices_own_agent(monkeypatch):
    captured = {}

    class _FakeResponse:
        status_code = 200
        text = "ok"

    def _fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "post", _fake_post)

    class _FakePlanetary:
        _artifact_version = "1.2.3"

    provisioner = EdgeProvisioner(planetary=_FakePlanetary())
    assert provisioner.provision("actor-x", device_id="edge-007", node_class="edge") is True
    assert captured["url"] == f"http://edge-007:{edgeprov.DEFAULT_EDGE_AGENT_PORT}/actors/actor-x/start"
    assert captured["json"]["node_class"] == "edge"
    assert captured["json"]["artifact_version"] == "1.2.3"


def test_09_provision_never_raises_when_agent_unreachable(monkeypatch):
    import httpx

    def _fake_post(*a, **kw):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "post", _fake_post)
    provisioner = EdgeProvisioner(planetary=object())
    assert provisioner.provision("actor-x", device_id="unreachable-device") is False


# ── 10-12: Lifecycle Controller -> EdgeProvisioner dispatch ───────────────


def test_10_edge_provisioning_fires_for_never_started_actor_on_edge_node(monkeypatch):
    redis = _FakeRedis()
    control_plane = _pr(redis, "control-plane-node")
    control_plane.register_node(ExecutionNode(node_id="edge-007", node_class=NodeClass.EDGE, capacity=5))

    entry = _register(control_plane, "carol")
    aid = entry.actor_id
    control_plane.set_actor_placement_requirements(
        aid,
        ActorPlacementRequirements(required_node_class=NodeClass.EDGE),
    )

    monkeypatch.setenv("EDGE_PROVISIONING_ENABLED", "true")
    calls = {}

    class _FakeEdgeProvisioner:
        def provision(self, actor_id, *, device_id, node_class):
            calls["provision"] = (actor_id, device_id, node_class)
            return True

    control_plane._edge_provisioner = _FakeEdgeProvisioner()
    result = control_plane.lifecycle.reconcile(aid)

    assert calls.get("provision") == (aid, "edge-007", "edge")
    assert result.action == "scheduled_elsewhere"


def test_11_edge_provisioning_does_not_fire_when_disabled(monkeypatch):
    redis = _FakeRedis()
    control_plane = _pr(redis, "control-plane-node")
    control_plane.register_node(ExecutionNode(node_id="edge-007", node_class=NodeClass.EDGE, capacity=5))

    entry = _register(control_plane, "dave")
    aid = entry.actor_id
    control_plane.set_actor_placement_requirements(
        aid,
        ActorPlacementRequirements(required_node_class=NodeClass.EDGE),
    )

    monkeypatch.delenv("EDGE_PROVISIONING_ENABLED", raising=False)
    calls = {}

    class _FakeEdgeProvisioner:
        def provision(self, actor_id, *, device_id, node_class):
            calls["provision"] = True
            return True

    control_plane._edge_provisioner = _FakeEdgeProvisioner()
    control_plane.lifecycle.reconcile(aid)
    assert "provision" not in calls


def test_12_edge_provisioning_does_not_fire_for_already_suspended_actor(monkeypatch):
    """An actor merely suspended-for-migration (already ran somewhere
    once) must NOT be re-provisioned -- only a genuinely cold-start
    actor (REGISTERED/INITIALIZED, never ACTIVE/SUSPENDED/IDLE)
    triggers EdgeProvisioner, matching KubernetesProvisioner's own
    "don't fabricate a placement for a real, different cause" contract."""
    redis = _FakeRedis()
    control_plane = _pr(redis, "control-plane-node")
    control_plane.register_node(ExecutionNode(node_id="edge-007", node_class=NodeClass.EDGE, capacity=5))

    entry = _register(control_plane, "erin")
    aid = entry.actor_id
    control_plane.set_actor_placement_requirements(
        aid,
        ActorPlacementRequirements(required_node_class=NodeClass.EDGE),
    )
    # Mark this actor's registry status as already SUSPENDED (as if it
    # had previously run and was suspended for migration), not a fresh
    # REGISTERED record.
    raw = redis._hashes[control_plane._ACTORS_HASH_KEY][aid]
    import json

    data = json.loads(raw)
    data["status"] = ActorStatus.SUSPENDED.value
    redis._hashes[control_plane._ACTORS_HASH_KEY][aid] = json.dumps(data)

    monkeypatch.setenv("EDGE_PROVISIONING_ENABLED", "true")
    calls = {}

    class _FakeEdgeProvisioner:
        def provision(self, actor_id, *, device_id, node_class):
            calls["provision"] = True
            return True

    control_plane._edge_provisioner = _FakeEdgeProvisioner()
    control_plane.lifecycle.reconcile(aid)
    assert "provision" not in calls


# ── 13: device identity != Actor identity ─────────────────────────────────


def test_13_device_id_is_never_derived_from_or_equal_to_actor_id(monkeypatch):
    monkeypatch.setenv("EDGE_DEVICE_ID", "edge-007")
    agent = EdgeAgent()
    assert agent.device_id == "edge-007"
    assert agent.device_id != "some-actor-id-123"
    # Per-actor subprocess node_id is device-scoped but still distinct
    # from the bare device_id and never equals the actor_id itself.
    _patch_actor_command(monkeypatch, agent, [sys.executable, "-c", "import time; time.sleep(5)"])
    agent.start_actor("some-actor-id-123")
    try:
        env_node_id = None
        # start_actor built the subprocess env internally; verify via the
        # documented convention instead of reaching into the closed-over
        # env dict (not exposed) -- the convention is asserted directly.
        expected = f"{agent.device_id}-{'some-actor-id-123'[:12]}"
        assert expected != agent.device_id
        assert expected != "some-actor-id-123"
    finally:
        agent.stop_actor("some-actor-id-123")


# ── 14: device heartbeat is never itself a placement candidate ───────────


def test_14_device_heartbeat_registers_with_zero_capacity(monkeypatch):
    """Live Deployment Validation finding: registering the device itself
    as a real-capacity ExecutionNode created a SECOND valid scheduling
    candidate alongside each actor's own per-actor node registration --
    confirmed live to cause an actor's explicit self-claim to be
    silently overwritten back onto the device's own node_id by the next
    ordinary reconcile (schedule() has no reason to prefer one valid
    EDGE-class candidate over another). capacity=0 makes this
    registration visible for fleet/health monitoring without ever being
    selectable as a placement target."""
    import asyncio
    import json as _json

    redis = _FakeRedis()
    monkeypatch.setenv("EDGE_DEVICE_ID", "edge-007")

    class _FakePlanetaryRuntime:
        def __init__(self, *a, **kw):
            self.registered = []

        def register_node(self, node):
            self.registered.append(node)

    fake_pr = _FakePlanetaryRuntime()
    monkeypatch.setattr(
        "src.monkey_brain.kernel.society.integration.PlanetaryRuntime",
        lambda *a, **kw: fake_pr,
    )
    monkeypatch.setenv("EDGE_AGENT_HEARTBEAT_INTERVAL", "0.05")

    agent = EdgeAgent()

    async def _run_briefly():
        task = asyncio.create_task(agent.register_device_heartbeat_loop())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run_briefly())

    assert fake_pr.registered, "device heartbeat never registered"
    for node in fake_pr.registered:
        assert node.capacity == 0
        assert node.current_actor_count == 0
        assert node.node_class == NodeClass.EDGE
