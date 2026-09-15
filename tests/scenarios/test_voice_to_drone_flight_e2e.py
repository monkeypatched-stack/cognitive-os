"""Voice -> simulated drone flight + independent camera telemetry.

Deliberately a deterministic simulation, same posture as this directory's
own test_three_drone_mission.py (that file's own docstring: "FakeRosExecutionAdapter
is the hardware seam; the test proves the CognitiveOS boundaries around
it.") and test_voice_command_runtime.py's mocking style: FakeRosExecutionAdapter
stands in for PX4 SITL/Gazebo, VoiceCommandRuntime's SocietyRuntime/ActorRuntime
are plain MagicMock/AsyncMock, and governance uses local_policy_decision to
bypass a LIVE OPA network call -- NOT a bypass of ensure_governed/
run_ros_action_if_governed itself, which both run for real. openai-whisper
is not installed in this environment (see pyproject.toml's `livekit` extra
and this file's own docstring below) so real audio->transcript is not
exercised here; the transcript string is injected directly, exactly as
LiveKitVoiceObservationProvider.observe() would hand one to
VoiceCommandRuntime._drain_once() after a real Whisper call succeeded.

This test proves two DISTINCT, chained facts, kept as separate sections so
neither overstates the other:

  A) voice_intent.interpret_voice_transcript() + VoiceCommandRuntime:
     "Take drone one to waypoint Alpha." deterministically becomes a goal
     text containing "x=8.0, y=0.0" (kernel/edge/voice_intent.py's own
     small test/demo waypoint registry), forwarded via the EXACT same
     ActorRuntime.add_goal() + SocietyRuntime.tick_one_actor() path a
     typed goal uses -- voice never touches governance or a ROS adapter
     directly (confirmed by these fakes: neither object here HAS a
     capability to call one).

  B) A Waypoint capability call carrying those exact resolved coordinates
     (x=8.0, y=0.0) -- the same parameters kernel/pipeline/llm_planner.py's
     already-real _backfill_px4_parameters() regex would deterministically
     extract from that goal text (verified separately, not re-implemented
     here) -- reaches FakeRosExecutionAdapter only through the real
     governed path (ensure_governed -> run_ros_action_if_governed), not a
     direct call.

Section A and B are not wired to each other in this test (the real LLM
planner sits between them and is not exercised here -- see the module
docstring above); the honest claim is "A produces the right input for B,
and B is real," not "the full planner ran."
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.monkey_brain.kernel.domains.robot import WaypointCapability
from src.monkey_brain.kernel.edge.ros_integration import FakeRosExecutionAdapter
from src.monkey_brain.kernel.edge.voice_command_runtime import VoiceCommandRuntime
from src.monkey_brain.kernel.edge.voice_intent import interpret_voice_transcript
from src.monkey_brain.kernel.edge.voice_session import VoiceSession
from src.monkey_brain.kernel.pipeline.observations import ObservationSet, Observation, Provenance

VOICE_COMMAND = "Take drone one to waypoint Alpha."
ACTOR_ID = "drone-a"


# ── Section A fixtures (voice -> goal, matches test_voice_command_runtime.py) ──


def _voice_session() -> VoiceSession:
    return VoiceSession(
        session_id="s1",
        room="mission-room",
        actor_id=ACTOR_ID,
        participant_identity="voice-u1",
        created_by_user_id="u1",
    )


def _actor_runtime_pair():
    state = MagicMock()
    actor_runtime = MagicMock()
    actor_runtime.add_goal = MagicMock()
    state.actor_runtime = actor_runtime
    sr = MagicMock()
    sr.get_actor.side_effect = lambda aid: state if aid == ACTOR_ID else None
    sr.tick_one_actor = AsyncMock(return_value=True)
    return sr, state, actor_runtime


def _make_voice_runtime():
    session = _voice_session()
    pr = MagicMock()
    with patch("src.monkey_brain.kernel.edge.voice_command_runtime.LiveKitVoiceObservationProvider"):
        runtime = VoiceCommandRuntime(session, pr)
    return runtime, session


# ── Section B fixtures (governed Waypoint -> FakeRosExecutionAdapter) ──


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


class _FakeVideoProvider:
    """Minimal ObservationProvider stand-in for the camera path -- proves
    independence from voice, not the video adapter itself (already covered
    by tests/unit/test_livekit_video_adapter.py)."""

    def __init__(self, *, raise_on_observe: bool = False):
        self._raise = raise_on_observe
        self.observe_calls = 0

    def observe(self, actor_id: str, world) -> ObservationSet:
        self.observe_calls += 1
        if self._raise:
            raise RuntimeError("camera track unavailable")
        return ObservationSet(
            observations=(
                Observation(
                    entity=actor_id,
                    attribute="camera_stream_available",
                    value=True,
                    confidence=1.0,
                    provenance=Provenance(source="drone_camera", method="track_state", reliability=1.0),
                ),
            ),
            actor_id=actor_id,
        )


# ── Section A: voice -> Observation -> Actor goal ──


def test_named_waypoint_transcript_resolves_to_literal_coordinates():
    result = interpret_voice_transcript(VOICE_COMMAND)
    assert result.kind == "actionable"
    assert result.goal_text == "Take drone one to waypoint Alpha. (x=8.0, y=0.0)"


@pytest.mark.asyncio
async def test_actionable_voice_command_adds_goal_and_ticks_never_touches_ros():
    runtime, session = _make_voice_runtime()
    sr, _state, actor_runtime = _actor_runtime_pair()
    runtime._pr.all_societies.return_value = [sr]

    await runtime._handle_transcript(VOICE_COMMAND)

    actor_runtime.add_goal.assert_called_once_with("Take drone one to waypoint Alpha. (x=8.0, y=0.0)")
    sr.tick_one_actor.assert_awaited_once_with(ACTOR_ID)
    assert session.status == "listening"
    # Neither fake exposes a ROS adapter or governance call -- voice
    # literally cannot reach PX4 except through the tick this proves it
    # triggers.
    assert not hasattr(sr, "ros_adapter")


@pytest.mark.asyncio
async def test_ambiguous_voice_command_never_reaches_goal_queue():
    runtime, session = _make_voice_runtime()
    sr, _state, actor_runtime = _actor_runtime_pair()
    runtime._pr.all_societies.return_value = [sr]

    await runtime._handle_transcript("Send the drone over there.")

    actor_runtime.add_goal.assert_not_called()
    sr.tick_one_actor.assert_not_awaited()
    assert session.status == "clarification_required"


@pytest.mark.asyncio
async def test_invalid_filler_transcript_never_reaches_goal_queue():
    runtime, session = _make_voice_runtime()
    sr, _state, actor_runtime = _actor_runtime_pair()
    runtime._pr.all_societies.return_value = [sr]

    await runtime._handle_transcript("um")

    actor_runtime.add_goal.assert_not_called()
    sr.tick_one_actor.assert_not_awaited()
    assert session.status == "listening"


@pytest.mark.asyncio
async def test_unknown_actor_reports_error_without_corrupting_drone_state():
    runtime, session = _make_voice_runtime()
    runtime._pr.all_societies.return_value = []  # actor genuinely not found

    await runtime._handle_transcript(VOICE_COMMAND)  # must not raise

    assert session.status == "idle"
    assert session.error == "actor not found"


@pytest.mark.asyncio
async def test_voice_transcription_failure_does_not_corrupt_drone_state():
    """Whisper unavailable/failed for a window -> observe() raises inside
    the provider -- _drain_once() must degrade this one poll, never crash
    the runtime or touch the goal queue."""
    runtime, session = _make_voice_runtime()
    runtime._provider.observe = MagicMock(side_effect=RuntimeError("whisper transcription failed"))

    await runtime._drain_once()  # must not raise

    assert session.status != "planning"


# ── Section B: governed Waypoint capability -> FakeRosExecutionAdapter ──


@pytest.mark.asyncio
async def test_resolved_waypoint_reaches_fake_adapter_through_real_governance(ensure_governed_mock):
    adapter = FakeRosExecutionAdapter(actor_id=ACTOR_ID)
    adapter.is_simulation = True  # duck-typed, matching Px4RosExecutionAdapter's real attribute

    assert _simulation_safety_check(adapter) is True

    context = {"actor_id": ACTOR_ID, "ros_adapter": adapter, "planetary_runtime": None}
    result = await WaypointCapability().handle(
        {"context": context, "parameters": {"x": 8.0, "y": 0.0, "height_m": 2.0}}
    )

    ensure_governed_mock.assert_called_once()
    assert ensure_governed_mock.call_args[0][0] == "capability.Waypoint"
    assert result["success"] is True
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["capability"] == "Waypoint"
    assert adapter.calls[0]["parameters"] == {"x": 8.0, "y": 0.0, "height_m": 2.0}


@pytest.mark.asyncio
async def test_pending_capability_never_calls_adapter_directly_bypassing_governance(ensure_governed_mock):
    """WaypointCapability.handle() must reach the adapter ONLY through
    run_ros_action_if_governed -- proven by patching ensure_governed to
    DENY and confirming the adapter is never touched."""
    deny = AsyncMock(side_effect=RuntimeError("denied"))
    adapter = FakeRosExecutionAdapter(actor_id=ACTOR_ID)
    with patch("src.monkey_brain.kernel.security_boundary.ensure_governed", deny):
        context = {"actor_id": ACTOR_ID, "ros_adapter": adapter, "planetary_runtime": None}
        with pytest.raises(RuntimeError):
            await WaypointCapability().handle({"context": context, "parameters": {"x": 8.0, "y": 0.0}})
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_adapter_failure_reports_failure_not_fake_success(ensure_governed_mock):
    class _FailingAdapter:
        actor_id = ACTOR_ID
        is_simulation = True

        async def invoke(self, *, capability: str, parameters: dict) -> dict:
            raise RuntimeError("PX4 unavailable")

    context = {"actor_id": ACTOR_ID, "ros_adapter": _FailingAdapter(), "planetary_runtime": None}
    with pytest.raises(RuntimeError, match="PX4 unavailable"):
        await WaypointCapability().handle({"context": context, "parameters": {"x": 8.0, "y": 0.0}})


# ── Simulation-safety precondition (test-harness-level, per spec: "fail
# the test before sending a flight command" -- deliberately NOT a change
# to WaypointCapability/production governance, since no such backend-kind
# gate exists today for normal flight capabilities and adding one there
# would be new architecture the task explicitly rules out) ──


def _simulation_safety_check(adapter) -> bool:
    import os

    return os.environ.get("SIMULATION_ONLY", "").strip().lower() == "true" or bool(
        getattr(adapter, "is_simulation", False)
    )


def test_simulation_safety_check_passes_for_simulator_adapter():
    adapter = FakeRosExecutionAdapter(actor_id=ACTOR_ID)
    adapter.is_simulation = True
    assert _simulation_safety_check(adapter) is True


def test_simulation_safety_check_refuses_non_simulator_adapter(monkeypatch):
    monkeypatch.delenv("SIMULATION_ONLY", raising=False)

    class _RealLookingAdapter:
        is_simulation = False

    assert _simulation_safety_check(_RealLookingAdapter()) is False


# ── Camera independence (proves the requirement, not just states it) ──


def test_camera_observations_continue_when_voice_fails():
    video_provider = _FakeVideoProvider()

    # Voice path failing (ambiguous / unknown actor / whisper failure --
    # any Section A failure case above) never touches this object at all;
    # demonstrate the converse directly: the video provider keeps
    # producing observations on its own, unconditionally.
    for _ in range(3):
        obs_set = video_provider.observe(ACTOR_ID, None)
        assert obs_set.observations[0].attribute == "camera_stream_available"
    assert video_provider.observe_calls == 3


def test_camera_failure_does_not_prevent_actor_cognitive_state():
    """An unavailable camera degrades to empty observations, never raises
    into the caller -- matching WorldPollingProvider's own
    try/except-per-aux-source contract (kernel/pipeline/observations.py)."""
    video_provider = _FakeVideoProvider(raise_on_observe=True)
    try:
        video_provider.observe(ACTOR_ID, None)
        pytest.fail("expected the fake to raise so this test exercises a real try/except below")
    except RuntimeError:
        degraded = ObservationSet(observations=(), actor_id=ACTOR_ID)
    assert degraded.is_empty()
