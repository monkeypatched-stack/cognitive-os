"""Unit tests for kernel/edge/video_command_runtime.py -- the bridge from a
drone-camera visual observation into the EXISTING belief/replanning path
(CognitiveOS Drone Camera Video Telemetry, spec TESTS #10/11/12:
"Observation entering the existing belief pipeline", "Visual observation
causing an existing replanning path to activate", "Governance remaining in
the execution path").

`security_boundary.ensure_governed` is patched with a stand-in that still
actually RUNS the effect it's given (not a no-op bypass) -- so these tests
prove two things at once: (1) VideoCommandRuntime calls the SAME governed
entry point api/routes/world.py's own POST /world/events uses, with the
right action name and skip_authz=True, and (2) that call genuinely results
in SharedWorld.record_event + tick_one_actor running, i.e. the observation
really does reach the existing tick/belief/replanning path, not a shortcut
around it. What is NOT re-tested here: that assert_state_mutation_allowed
itself enforces a commitment -- that's SharedWorld's own concern, already
covered by scripts/check_architecture_conformance.py's
shared_world_mutations_require_commitment check (verified passing after
this feature's own code, including this call site, was added)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.monkey_brain.kernel.edge.video_command_runtime import VideoCommandRuntime
from src.monkey_brain.kernel.edge.video_session import VideoSession
from src.monkey_brain.kernel.pipeline.observations import Observation, Provenance


def _session() -> VideoSession:
    return VideoSession(
        session_id="s1", room="r", actor_id="drone-a", camera_track_name="drone-a-camera-track", created_by_user_id="u1"
    )


def _fake_pr(actor_id: str = "drone-a"):
    state = MagicMock()
    sr = MagicMock()
    sr.get_actor.side_effect = lambda aid: state if aid == actor_id else None
    sr.tick_one_actor = AsyncMock(return_value=True)
    sr.world = MagicMock()
    pr = MagicMock()
    pr.all_societies.return_value = [sr]
    return pr, sr


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


def _obs(attribute: str, value, confidence: float = 0.8) -> Observation:
    return Observation(
        entity="drone-a-camera-track",
        attribute=attribute,
        value=value,
        confidence=confidence,
        provenance=Provenance(source="drone_camera", method="visual_perception", reliability=confidence),
    )


@pytest.mark.asyncio
async def test_camera_stream_available_updates_session_without_world_write(ensure_governed_mock):
    session = _session()
    pr, sr = _fake_pr()
    runtime = VideoCommandRuntime(session, pr)

    await runtime._handle_observation(_obs("camera_stream_available", False, confidence=1.0))

    assert session.stream_available is False
    assert session.status == "degraded"
    sr.world.record_event.assert_not_called()
    sr.tick_one_actor.assert_not_called()
    ensure_governed_mock.assert_not_called()


@pytest.mark.asyncio
async def test_consequential_observation_writes_world_event_and_ticks(ensure_governed_mock):
    session = _session()
    pr, sr = _fake_pr()
    runtime = VideoCommandRuntime(session, pr)

    await runtime._handle_observation(_obs("obstacle_detected", True))

    ensure_governed_mock.assert_called_once()
    args, kwargs = ensure_governed_mock.call_args
    assert args[0] == "video.observation.record"
    assert kwargs.get("skip_authz") is True

    sr.world.record_event.assert_called_once()
    event = sr.world.record_event.call_args[0][0]
    assert event.attributes["obstacle_detected"] is True
    assert event.entity_id == "drone-a"
    assert event.source_actor_id == "drone-a"

    sr.tick_one_actor.assert_awaited_once_with("drone-a")
    assert session.last_observation_attribute == "obstacle_detected"
    assert session.last_observation_value is True


@pytest.mark.asyncio
async def test_repeated_same_value_is_debounced(ensure_governed_mock):
    session = _session()
    pr, sr = _fake_pr()
    runtime = VideoCommandRuntime(session, pr)

    await runtime._handle_observation(_obs("obstacle_detected", True))
    await runtime._handle_observation(_obs("obstacle_detected", True))

    assert sr.tick_one_actor.await_count == 1
    assert sr.world.record_event.call_count == 1


@pytest.mark.asyncio
async def test_changed_value_retriggers_write_and_tick(ensure_governed_mock):
    session = _session()
    pr, sr = _fake_pr()
    runtime = VideoCommandRuntime(session, pr)

    await runtime._handle_observation(_obs("obstacle_detected", True))
    await runtime._handle_observation(_obs("obstacle_detected", False))

    assert sr.tick_one_actor.await_count == 2
    assert sr.world.record_event.call_count == 2


@pytest.mark.asyncio
async def test_unknown_actor_sets_error_without_crashing(ensure_governed_mock):
    session = _session()
    pr, sr = _fake_pr(actor_id="some-other-drone")
    runtime = VideoCommandRuntime(session, pr)

    await runtime._handle_observation(_obs("obstacle_detected", True))  # must not raise

    assert session.error == "actor not found"
    sr.tick_one_actor.assert_not_called()


@pytest.mark.asyncio
async def test_tick_failure_is_caught_and_recorded_not_raised(ensure_governed_mock):
    session = _session()
    pr, sr = _fake_pr()
    sr.tick_one_actor = AsyncMock(side_effect=RuntimeError("boom"))
    runtime = VideoCommandRuntime(session, pr)

    await runtime._handle_observation(_obs("obstacle_detected", True))  # must not raise

    assert session.error == "boom"
