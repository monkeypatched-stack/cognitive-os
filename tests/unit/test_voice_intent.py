"""Unit tests for kernel/edge/voice_intent.py -- the voice command
ambiguity gate (CognitiveOS LiveKit Voice Command Integration, spec
section 24's "Intent" test group: valid command, ambiguous command,
malformed transcript, missing target)."""

from __future__ import annotations

from src.monkey_brain.kernel.edge.voice_intent import interpret_voice_transcript


def test_valid_command_with_named_waypoint_is_actionable():
    result = interpret_voice_transcript("Send drone one to waypoint Alpha.")
    assert result.kind == "actionable"
    assert result.goal_text == "Send drone one to waypoint Alpha."
    assert result.clarification_reason is None


def test_valid_command_with_coordinates_is_actionable():
    result = interpret_voice_transcript("Take off to 10 meters, fly to x=5 y=5, then land.")
    assert result.kind == "actionable"
    assert result.goal_text is not None


def test_valid_command_without_movement_language_is_actionable():
    # Not every actionable voice command is a movement command (e.g. "land
    # now", "arm the drone") -- the gate only special-cases the specific
    # ambiguity pattern (movement verb + bare pronoun), everything else
    # passes straight through to the existing planner.
    result = interpret_voice_transcript("Land now.")
    assert result.kind == "actionable"


def test_ambiguous_pronoun_destination_is_flagged_not_executed():
    # The spec's own literal example (section 10).
    result = interpret_voice_transcript("Send the drone over there.")
    assert result.kind == "ambiguous"
    assert result.goal_text is None
    assert "waypoint" in (result.clarification_reason or "").lower()


def test_ambiguous_here_without_anchor_is_flagged():
    result = interpret_voice_transcript("Go here.")
    assert result.kind == "ambiguous"
    assert result.goal_text is None


def test_pronoun_with_named_waypoint_anchor_is_not_ambiguous():
    # "there" is present, but the sentence also gives a real anchor -- the
    # heuristic must not fire on every pronoun, only an UNRESOLVED one.
    result = interpret_voice_transcript("Go there, to waypoint Bravo.")
    assert result.kind == "actionable"


def test_pronoun_with_coordinates_anchor_is_not_ambiguous():
    result = interpret_voice_transcript("Send it there, x=5 y=7.")
    assert result.kind == "actionable"


def test_empty_transcript_is_non_actionable():
    result = interpret_voice_transcript("")
    assert result.kind == "non_actionable"
    assert result.goal_text is None


def test_whitespace_only_transcript_is_non_actionable():
    result = interpret_voice_transcript("   ")
    assert result.kind == "non_actionable"


def test_filler_transcript_is_non_actionable():
    for filler in ("um", "uh", "hi", "hey", "hello", "testing", "ok", "okay"):
        result = interpret_voice_transcript(filler)
        assert result.kind == "non_actionable", f"{filler!r} should be non_actionable, got {result.kind}"


def test_none_like_malformed_transcript_does_not_raise():
    # Whisper can hand back a degenerate string on a noisy/failed window;
    # this must never raise (LiveKitVoiceObservationProvider.observe()
    # itself must never crash the cognitive runtime — section 6.7).
    result = interpret_voice_transcript(None)  # type: ignore[arg-type]
    assert result.kind == "non_actionable"


def test_result_never_produces_a_goal_for_ambiguous_or_non_actionable():
    for transcript in ("", "um", "send the drone over there"):
        result = interpret_voice_transcript(transcript)
        if result.kind != "actionable":
            assert result.goal_text is None
