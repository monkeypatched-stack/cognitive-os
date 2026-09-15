"""Unit tests for kernel/domains/robot.py::CrashTestCapability -- the
simulator-only crash-test demo behavior's own pre-governance safety
checks (layer 1 of the 4-layer safety architecture; see that class's own
docstring for the other three). No real ROS/rclpy, OPA server, or
Px4RosExecutionAdapter construction needed -- `ros_adapter` is a small
fake object, `ensure_governed` is patched with a stand-in that still
executes the wrapped effect (same fixture shape as
test_video_command_runtime.py's ensure_governed_mock, never a bypass)."""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, patch

import pytest

from src.monkey_brain.kernel.domains.robot import CrashTestCapability


class _FakeFact:
    def __init__(self, entity: str, attribute: str, value, confidence: float = 1.0):
        self.entity = entity
        self.attribute = attribute
        self.value = value
        self.confidence = confidence


class _FakeAdapter:
    def __init__(self, *, is_simulation: bool = True):
        self.is_simulation = is_simulation
        self.invoke_calls: list[dict] = []

    async def invoke(self, *, capability: str, parameters: dict) -> dict:
        self.invoke_calls.append({"capability": capability, "parameters": parameters})
        return {"success": True, "collision": True, "landmark_id": parameters.get("landmark_id")}


def _context(actor_id: str, adapter, facts: list) -> dict:
    belief = types.SimpleNamespace(facts=facts)
    cognitive_actor = types.SimpleNamespace(pipeline_belief=lambda: belief)
    state = types.SimpleNamespace(actor=cognitive_actor)
    sr = types.SimpleNamespace(get_actor=lambda aid: state if aid == actor_id else None)
    pr = types.SimpleNamespace(all_societies=lambda: [sr])
    return {"actor_id": actor_id, "ros_adapter": adapter, "planetary_runtime": pr}


def _verified_fact(actor_id: str, landmark_id: str, *, confidence: float = 0.9, geometric_verified: bool = True):
    return _FakeFact(
        entity=actor_id,
        attribute="visual_landmark_match",
        value={"landmark_id": landmark_id, "geometric_verified": geometric_verified, "match_score": confidence},
        confidence=confidence,
    )


async def _run_effect(action, resource, effect, **kwargs):
    result = effect()
    if asyncio.iscoroutine(result):
        result = await result
    return result


@pytest.fixture
def ensure_governed_mock():
    mock = AsyncMock(side_effect=_run_effect)
    with patch("src.monkey_brain.kernel.security_boundary.ensure_governed", mock):
        yield mock


def _clear_env(monkeypatch) -> None:
    for name in ("CRASH_TEST_MODE", "SIMULATION_ONLY", "CRASH_TEST_TARGETS"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_disabled_by_default(monkeypatch, ensure_governed_mock):
    _clear_env(monkeypatch)
    adapter = _FakeAdapter()
    context = _context("drone-a", adapter, [_verified_fact("drone-a", "house_alpha")])

    result = await CrashTestCapability().handle({"context": context, "parameters": {"landmark_id": "house_alpha"}})

    assert result["success"] is False
    assert adapter.invoke_calls == []
    ensure_governed_mock.assert_not_called()


@pytest.mark.asyncio
async def test_requires_both_simulation_only_and_crash_test_mode(monkeypatch, ensure_governed_mock):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_MODE", "true")  # SIMULATION_ONLY still unset
    adapter = _FakeAdapter()
    context = _context("drone-a", adapter, [_verified_fact("drone-a", "house_alpha")])

    result = await CrashTestCapability().handle({"context": context, "parameters": {"landmark_id": "house_alpha"}})

    assert result["success"] is False
    assert adapter.invoke_calls == []


@pytest.mark.asyncio
async def test_real_non_simulation_adapter_is_rejected(monkeypatch, ensure_governed_mock):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_MODE", "true")
    monkeypatch.setenv("SIMULATION_ONLY", "true")
    adapter = _FakeAdapter(is_simulation=False)  # simulates a hypothetical future real-hardware adapter
    context = _context("drone-a", adapter, [_verified_fact("drone-a", "house_alpha")])

    result = await CrashTestCapability().handle({"context": context, "parameters": {"landmark_id": "house_alpha"}})

    assert result["success"] is False
    assert adapter.invoke_calls == []


@pytest.mark.asyncio
async def test_unverified_landmark_cannot_arm(monkeypatch, ensure_governed_mock):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_MODE", "true")
    monkeypatch.setenv("SIMULATION_ONLY", "true")
    adapter = _FakeAdapter()
    context = _context("drone-a", adapter, [])  # no belief facts at all

    result = await CrashTestCapability().handle({"context": context, "parameters": {"landmark_id": "house_alpha"}})

    assert result["success"] is False
    assert "not a geometric_verified" in result["error"]
    assert adapter.invoke_calls == []


@pytest.mark.asyncio
async def test_plain_unverified_observation_cannot_arm(monkeypatch, ensure_governed_mock):
    """A candidate/low-confidence match that never passed LoFTR's own
    geometric verification must not satisfy this check -- geometric_verified
    must be explicitly True, not merely present."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_MODE", "true")
    monkeypatch.setenv("SIMULATION_ONLY", "true")
    adapter = _FakeAdapter()
    facts = [_verified_fact("drone-a", "house_alpha", geometric_verified=False)]
    context = _context("drone-a", adapter, facts)

    result = await CrashTestCapability().handle({"context": context, "parameters": {"landmark_id": "house_alpha"}})

    assert result["success"] is False
    assert adapter.invoke_calls == []


@pytest.mark.asyncio
async def test_wrong_landmark_cannot_be_selected(monkeypatch, ensure_governed_mock):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_MODE", "true")
    monkeypatch.setenv("SIMULATION_ONLY", "true")
    adapter = _FakeAdapter()
    # house_beta is verified, but the request asks for house_alpha.
    facts = [_verified_fact("drone-a", "house_beta")]
    context = _context("drone-a", adapter, facts)

    result = await CrashTestCapability().handle({"context": context, "parameters": {"landmark_id": "house_alpha"}})

    assert result["success"] is False
    assert adapter.invoke_calls == []


@pytest.mark.asyncio
async def test_missing_target_mapping_is_rejected(monkeypatch, ensure_governed_mock):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_MODE", "true")
    monkeypatch.setenv("SIMULATION_ONLY", "true")
    # No CRASH_TEST_TARGETS set -> no simulator coordinates for any landmark.
    adapter = _FakeAdapter()
    context = _context("drone-a", adapter, [_verified_fact("drone-a", "house_alpha")])

    result = await CrashTestCapability().handle({"context": context, "parameters": {"landmark_id": "house_alpha"}})

    assert result["success"] is False
    assert "no simulator target configured" in result["error"]
    assert adapter.invoke_calls == []


@pytest.mark.asyncio
async def test_low_confidence_verified_landmark_cannot_arm(monkeypatch, ensure_governed_mock):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_MODE", "true")
    monkeypatch.setenv("SIMULATION_ONLY", "true")
    monkeypatch.setenv("CRASH_TEST_TARGETS", '{"house_alpha": [15.0, 0.0, -3.0]}')
    adapter = _FakeAdapter()
    facts = [_verified_fact("drone-a", "house_alpha", confidence=0.1)]  # below default 0.7 threshold
    context = _context("drone-a", adapter, facts)

    result = await CrashTestCapability().handle({"context": context, "parameters": {"landmark_id": "house_alpha"}})

    assert result["success"] is False
    assert adapter.invoke_calls == []


@pytest.mark.asyncio
async def test_fully_armed_request_reaches_governance_and_adapter(monkeypatch, ensure_governed_mock):
    """The success path still goes through run_ros_action_if_governed's
    real ensure_governed() call -- governance is never skipped just
    because every capability-level safety check passed."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_MODE", "true")
    monkeypatch.setenv("SIMULATION_ONLY", "true")
    monkeypatch.setenv("CRASH_TEST_TARGETS", '{"house_alpha": [15.0, 0.0, -3.0]}')
    adapter = _FakeAdapter()
    context = _context("drone-a", adapter, [_verified_fact("drone-a", "house_alpha")])

    result = await CrashTestCapability().handle({"context": context, "parameters": {"landmark_id": "house_alpha"}})

    ensure_governed_mock.assert_called_once()
    args, kwargs = ensure_governed_mock.call_args
    assert args[0] == "capability.CrashTest"
    assert kwargs.get("extra", {}).get("signals", {}) == {
        "simulation_only": True,
        "crash_test_mode": True,
        "is_simulation": True,
    }
    assert len(adapter.invoke_calls) == 1
    assert adapter.invoke_calls[0]["capability"] == "CrashTest"
    assert adapter.invoke_calls[0]["parameters"]["landmark_id"] == "house_alpha"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_no_ros_adapter_bound_is_rejected(ensure_governed_mock):
    context = {"actor_id": "drone-a", "ros_adapter": None, "planetary_runtime": None}

    result = await CrashTestCapability().handle({"context": context, "parameters": {"landmark_id": "house_alpha"}})

    assert result["success"] is False
    assert "no ROS adapter" in result["error"]
