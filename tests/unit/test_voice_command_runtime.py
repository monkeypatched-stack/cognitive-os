"""Unit tests for kernel/edge/voice_command_runtime.py -- verifies the
"voice must never bypass governance" requirement (CognitiveOS LiveKit
Voice Command Integration, spec section 11) at the unit level: an
ambiguous or non-actionable transcript must produce ZERO calls into the
actor's goal queue or tick path (the only way anything reaches governance
or the drone adapter in this codebase), while an actionable one must
produce exactly one add_goal() + one tick_one_actor() call with the
transcript forwarded unedited (section 26: never a second planner, only
the existing goal/tick path).

LiveKitVoiceObservationProvider itself is mocked out here -- its own real
behavior (room connection, Whisper transcription, never-raises contract)
is exercised separately wherever kernel/edge/livekit_adapter.py is tested;
this file is only about what VoiceCommandRuntime does with the transcripts
that provider hands it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.monkey_brain.kernel.edge.voice_command_runtime import VoiceCommandRuntime
from src.monkey_brain.kernel.edge.voice_session import VoiceSessionStore
from src.monkey_brain.kernel.pipeline.observations import Observation, ObservationSet, Provenance


def _make_runtime(store: VoiceSessionStore):
    session = store.create(room="r", actor_id="drone-a", participant_identity="voice-u1", created_by_user_id="u1")
    with patch("src.monkey_brain.kernel.edge.voice_command_runtime.LiveKitVoiceObservationProvider") as provider_cls:
        provider = MagicMock()
        provider_cls.return_value = provider
        runtime = VoiceCommandRuntime(session, planetary_runtime=MagicMock())
    return runtime, session, provider


def _actor_runtime_pair():
    """A fake (SocietyRuntime, ActorState) pair matching
    _find_actor_state's contract: pr.all_societies() -> [sr],
    sr.get_actor(id) -> state, state.actor_runtime.add_goal(...),
    sr.tick_one_actor(id) (async)."""
    actor_runtime = MagicMock()
    state = MagicMock()
    state.actor_runtime = actor_runtime
    sr = MagicMock()
    sr.get_actor.return_value = state
    sr.tick_one_actor = AsyncMock(return_value=True)
    return sr, state, actor_runtime


@pytest.mark.asyncio
async def test_actionable_transcript_adds_goal_and_ticks():
    store = VoiceSessionStore()
    runtime, session, _provider = _make_runtime(store)
    sr, _state, actor_runtime = _actor_runtime_pair()
    runtime._pr.all_societies.return_value = [sr]

    await runtime._handle_transcript("Send drone one to waypoint Alpha.")

    # "Alpha" is a known test/demo waypoint (kernel/edge/voice_intent.py::
    # _TEST_WAYPOINT_COORDINATES) -- its coordinates are appended so the
    # existing planner-side backfill resolves them deterministically.
    enriched = "Send drone one to waypoint Alpha. (x=8.0, y=0.0)"
    actor_runtime.add_goal.assert_called_once_with(enriched)
    sr.tick_one_actor.assert_awaited_once_with("drone-a")
    assert session.status == "listening"
    assert session.last_goal_text == enriched


@pytest.mark.asyncio
async def test_ambiguous_transcript_never_reaches_goal_queue_or_tick():
    store = VoiceSessionStore()
    runtime, session, _provider = _make_runtime(store)
    sr, _state, actor_runtime = _actor_runtime_pair()
    runtime._pr.all_societies.return_value = [sr]

    await runtime._handle_transcript("Send the drone over there.")

    actor_runtime.add_goal.assert_not_called()
    sr.tick_one_actor.assert_not_awaited()
    assert session.status == "clarification_required"
    assert session.clarification_reason is not None


@pytest.mark.asyncio
async def test_non_actionable_transcript_never_reaches_goal_queue_or_tick():
    store = VoiceSessionStore()
    runtime, session, _provider = _make_runtime(store)
    sr, _state, actor_runtime = _actor_runtime_pair()
    runtime._pr.all_societies.return_value = [sr]

    await runtime._handle_transcript("um")

    actor_runtime.add_goal.assert_not_called()
    sr.tick_one_actor.assert_not_awaited()
    assert session.status == "listening"


@pytest.mark.asyncio
async def test_missing_actor_is_reported_without_raising():
    store = VoiceSessionStore()
    runtime, session, _provider = _make_runtime(store)
    runtime._pr.all_societies.return_value = []  # actor genuinely not found

    await runtime._handle_transcript("Send drone one to waypoint Alpha.")

    assert session.status == "idle"
    assert session.error == "actor not found"


@pytest.mark.asyncio
async def test_tick_exception_is_caught_and_recorded_not_raised():
    store = VoiceSessionStore()
    runtime, session, _provider = _make_runtime(store)
    sr, _state, actor_runtime = _actor_runtime_pair()
    sr.tick_one_actor = AsyncMock(side_effect=RuntimeError("boom"))
    runtime._pr.all_societies.return_value = [sr]

    # Must not raise -- a transient failure degrades this one voice
    # session, never crashes the caller (section 6.7 / 19).
    await runtime._handle_transcript("Send drone one to waypoint Alpha.")

    assert session.status == "idle"
    assert "boom" in (session.error or "")


@pytest.mark.asyncio
async def test_drain_once_only_forwards_voice_transcript_observations():
    store = VoiceSessionStore()
    runtime, _session, provider = _make_runtime(store)
    sr, _state, actor_runtime = _actor_runtime_pair()
    runtime._pr.all_societies.return_value = [sr]

    provider.observe.return_value = ObservationSet(
        actor_id="drone-a",
        observations=(
            Observation(
                entity="drone-a",
                attribute="battery_level",
                value=90,
                provenance=Provenance(source="px4_ros", method="telemetry"),
            ),
            Observation(
                entity="voice-u1",
                attribute="voice_transcript",
                value="Land now.",
                provenance=Provenance(source="livekit_voice", method="whisper_transcribe"),
            ),
        ),
    )

    await runtime._drain_once()

    actor_runtime.add_goal.assert_called_once_with("Land now.")


@pytest.mark.asyncio
async def test_observe_failure_does_not_raise():
    store = VoiceSessionStore()
    runtime, _session, provider = _make_runtime(store)
    provider.observe.side_effect = RuntimeError("livekit connection dropped")

    # Must not raise.
    await runtime._drain_once()
