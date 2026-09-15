"""Unit tests for kernel/edge/voice_session.py -- the minimal voice session
correlation state (CognitiveOS LiveKit Voice Command Integration, spec
section 13)."""

from __future__ import annotations

from src.monkey_brain.kernel.edge.voice_session import VoiceSessionStore


def test_create_returns_session_with_expected_fields():
    store = VoiceSessionStore()
    session = store.create(room="room-1", actor_id="drone-a", participant_identity="voice-u1", created_by_user_id="u1")
    assert session.room == "room-1"
    assert session.actor_id == "drone-a"
    assert session.participant_identity == "voice-u1"
    assert session.created_by_user_id == "u1"
    assert session.status == "listening"
    assert session.session_id


def test_get_returns_same_session_by_id():
    store = VoiceSessionStore()
    session = store.create(room="r", actor_id="a", participant_identity="p", created_by_user_id="u")
    fetched = store.get(session.session_id)
    assert fetched is session


def test_get_unknown_id_returns_none():
    store = VoiceSessionStore()
    assert store.get("does-not-exist") is None


def test_update_mutates_and_returns_session():
    store = VoiceSessionStore()
    session = store.create(room="r", actor_id="a", participant_identity="p", created_by_user_id="u")
    updated = store.update(session.session_id, status="clarification_required", last_transcript="go there")
    assert updated is session
    assert session.status == "clarification_required"
    assert session.last_transcript == "go there"


def test_update_unknown_id_returns_none():
    store = VoiceSessionStore()
    assert store.update("does-not-exist", status="idle") is None


def test_remove_deletes_and_returns_session():
    store = VoiceSessionStore()
    session = store.create(room="r", actor_id="a", participant_identity="p", created_by_user_id="u")
    removed = store.remove(session.session_id)
    assert removed is session
    assert store.get(session.session_id) is None


def test_remove_unknown_id_returns_none():
    store = VoiceSessionStore()
    assert store.remove("does-not-exist") is None


def test_two_sessions_are_independent():
    store = VoiceSessionStore()
    s1 = store.create(room="r", actor_id="a1", participant_identity="p1", created_by_user_id="u1")
    s2 = store.create(room="r", actor_id="a2", participant_identity="p2", created_by_user_id="u2")
    store.update(s1.session_id, status="idle")
    assert s2.status == "listening"
