"""SocietyRuntime._message_queue cross-process gap — qualification tests.

Prior state: send_message()/broadcast_message() queued into a plain
process-local list (SocietyRuntime._message_queue) — only the SAME
process that appended to it could ever drain it via _deliver_messages(),
so a message addressed to an actor another process was ticking was
structurally invisible there, by construction, regardless of any
communication/affiliation eligibility check passing.

Fix: PlanetaryRuntime.push_actor_message()/drain_actor_inbox()/
peek_actor_inbox() give each actor_id a durable, per-actor Redis inbox.
SocietyRuntime.send_message()/_deliver_messages()/get_messages_for() now
route through these when a PlanetaryRuntime/Redis is available, falling
back to the exact prior in-process-list behavior otherwise (verified by
test_get_messages_for_falls_back_to_local_queue_without_redis, which
mirrors an already-passing existing test in
tests/unit/test_correlation_causation.py).

Two test groups:
  1. The core mechanism (push/drain/peek), tested directly against
     PlanetaryRuntime with no SocietyRuntime/communication-router/
     geography involved at all — the actual cross-process guarantee.
  2. SocietyRuntime integration — confirms send_message()/
     _deliver_messages() correctly route through the durable path when
     available, and correctly fall back when not.

Per this repo's session convention, this file is written but not executed
by the assistant. Run with:
    python -m pytest tests/unit/test_actor_messaging_cross_process.py -v
"""

from __future__ import annotations

import os

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
from src.monkey_brain.kernel.society.belief import BeliefState


class _FakePipeline:
    """Minimal stand-in for redis-py's pipeline() — push_actor_message()
    queues rpush+expire then calls execute()."""

    def __init__(self, redis: "_FakeMessagingRedis") -> None:
        self._redis = redis
        self._ops: list[tuple] = []

    def rpush(self, key, value):
        self._ops.append(("rpush", key, value))
        return self

    def expire(self, key, seconds):
        self._ops.append(("expire", key, seconds))
        return self

    def execute(self):
        results = []
        for op, key, value in self._ops:
            if op == "rpush":
                results.append(self._redis.rpush(key, value))
            elif op == "expire":
                results.append(self._redis.expire(key, value))
        self._ops = []
        return results


class _FakeMessagingRedis:
    """Minimal in-memory stand-in for the subset of redis-py's API
    push_actor_message/drain_actor_inbox/peek_actor_inbox actually use:
    pipeline(rpush+expire), lrange, and a real Lua-script emulation for
    the atomic drain (lrange + del, matching _DRAIN_INBOX_SCRIPT's exact
    behavior — including its "only DEL if non-empty" guard)."""

    def __init__(self) -> None:
        self._lists: dict[str, list[str]] = {}
        self._expiry: dict[str, int] = {}

    def ping(self) -> bool:
        return True

    def rpush(self, key, value):
        self._lists.setdefault(key, []).append(value)
        return len(self._lists[key])

    def expire(self, key, seconds):
        self._expiry[key] = seconds
        return True

    def lrange(self, key, start, end):
        lst = self._lists.get(key, [])
        return list(lst) if end == -1 else lst[start : end + 1]

    def delete(self, key):
        return 1 if self._lists.pop(key, None) is not None else 0

    def pipeline(self):
        return _FakePipeline(self)

    def eval(self, script, numkeys, key):
        # Emulates _DRAIN_INBOX_SCRIPT exactly: lrange, then del ONLY if
        # non-empty (matching the real script's "if #msgs > 0" guard).
        msgs = list(self._lists.get(key, []))
        if msgs:
            self._lists.pop(key, None)
        return msgs


def _register(pr: PlanetaryRuntime, name: str, **kwargs):
    return pr.register_actor(
        ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.AI_AGENT)),
        **kwargs,
    )


# ── Group 1: the core cross-process mechanism ───────────────────────────


def test_push_and_drain_round_trips_a_message_across_two_planetary_runtime_instances():
    """The literal cross-process guarantee: pr1 pushes, pr2 (an
    independent instance, standing in for a different process) drains —
    with no SocietyRuntime, no communication router, no shared society at
    all involved. If this passes, the mechanism itself is sound
    regardless of how SocietyRuntime chooses to use it."""
    redis = _FakeMessagingRedis()
    pr1 = PlanetaryRuntime()
    pr1._redis = redis
    pr2 = PlanetaryRuntime()
    pr2._redis = redis

    message = {
        "from": "alice",
        "to": "bob",
        "type": "greeting",
        "payload": {"text": "hi"},
    }
    assert pr1.push_actor_message("bob", message) is True

    drained = pr2.drain_actor_inbox("bob")
    assert drained == [message]


def test_drain_clears_the_inbox_atomically():
    redis = _FakeMessagingRedis()
    pr = PlanetaryRuntime()
    pr._redis = redis
    pr.push_actor_message("bob", {"from": "alice", "type": "x", "payload": {}})

    first = pr.drain_actor_inbox("bob")
    second = pr.drain_actor_inbox("bob")
    assert len(first) == 1
    assert second == []


def test_peek_does_not_clear_the_inbox():
    redis = _FakeMessagingRedis()
    pr = PlanetaryRuntime()
    pr._redis = redis
    pr.push_actor_message("bob", {"from": "alice", "type": "x", "payload": {}})

    peeked_once = pr.peek_actor_inbox("bob")
    peeked_twice = pr.peek_actor_inbox("bob")
    drained = pr.drain_actor_inbox("bob")
    assert len(peeked_once) == 1
    assert len(peeked_twice) == 1  # still there — peek is non-destructive
    assert len(drained) == 1  # drain finally clears it


def test_push_and_drain_degrade_gracefully_without_redis():
    pr = PlanetaryRuntime()  # no real Redis in the test environment -> self._redis is None
    assert pr.push_actor_message("bob", {"from": "alice"}) is False
    assert pr.drain_actor_inbox("bob") == []
    assert pr.peek_actor_inbox("bob") == []


def test_multiple_actors_have_independent_inboxes():
    redis = _FakeMessagingRedis()
    pr = PlanetaryRuntime()
    pr._redis = redis
    pr.push_actor_message("bob", {"from": "alice", "type": "to-bob", "payload": {}})
    pr.push_actor_message("carol", {"from": "alice", "type": "to-carol", "payload": {}})

    bob_inbox = pr.drain_actor_inbox("bob")
    carol_inbox = pr.peek_actor_inbox("carol")
    assert [m["type"] for m in bob_inbox] == ["to-bob"]
    assert [m["type"] for m in carol_inbox] == ["to-carol"]


# ── Group 2: SocietyRuntime integration ─────────────────────────────────


def test_send_message_uses_the_durable_inbox_when_redis_is_available():
    pr = PlanetaryRuntime()
    pr._redis = _FakeMessagingRedis()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")
    sr = pr._society_runtime

    sent = sr.send_message(alice.actor_id, bob.actor_id, "greeting", {"text": "hi"})

    assert sent is True
    assert sr._message_queue == []  # did NOT fall back to the local queue
    durable = pr.peek_actor_inbox(bob.actor_id)
    assert len(durable) == 1
    assert durable[0]["from"] == alice.actor_id
    assert durable[0]["payload"]["text"] == "hi"


def test_get_messages_for_falls_back_to_local_queue_without_redis():
    """Mirrors tests/unit/test_correlation_causation.py's own already-
    passing test_queued_message_carries_the_decisions_correlation_id —
    confirms the fix did not change behavior for the no-Redis case this
    repo's existing test suite already exercises."""
    pr = PlanetaryRuntime()  # no real Redis in the test environment
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")
    sr = pr._society_runtime

    sent = sr.send_message(
        alice.actor_id,
        bob.actor_id,
        "greeting",
        {"text": "hi"},
        correlation_id="corr-1",
    )

    assert sent is True
    queued = sr.get_messages_for(bob.actor_id)
    assert len(queued) == 1
    assert queued[0]["correlation_id"] == "corr-1"
    assert queued[0]["payload"]["text"] == "hi"


def test_deliver_messages_drains_the_durable_inbox_and_injects_a_trust_weighted_belief(
    monkeypatch,
):
    pr = PlanetaryRuntime()
    pr._redis = _FakeMessagingRedis()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")
    sr = pr._society_runtime

    bob_state = sr.get_actor(bob.actor_id)
    bob_state.belief_state = BeliefState()  # real, minimal belief_state so delivery has something to inject into
    # Trust >= 0.3 is what gates delivery in _deliver_messages(); this
    # test is about the NEW durable-inbox delivery path, not about
    # whichever trust-engine/affiliation defaults a freshly-registered
    # actor happens to get, so fix trust deterministically rather than
    # depend on unverified defaults from an unrelated subsystem.
    monkeypatch.setattr(sr, "get_trust", lambda from_actor, to_actor: 1.0)

    sr.send_message(alice.actor_id, bob.actor_id, "greeting", {"message": "hello bob"})
    assert pr.peek_actor_inbox(bob.actor_id)  # confirmed queued durably before delivery

    delivered = sr._deliver_messages()

    assert delivered == 1
    assert pr.peek_actor_inbox(bob.actor_id) == []  # drained, not left behind
    beliefs = sr.get_actor(bob.actor_id).belief_state.beliefs
    assert len(beliefs) == 1
    assert "hello bob" in beliefs[0].subject


def test_deliver_messages_only_drains_actors_resident_in_this_process():
    """An inbox for an actor_id this SocietyRuntime does NOT have resident
    must be left untouched by _deliver_messages() — it belongs to
    whichever process actually has that actor loaded."""
    pr = PlanetaryRuntime()
    pr._redis = _FakeMessagingRedis()
    alice = _register(pr, "Alice")
    sr = pr._society_runtime

    pr.push_actor_message("someone-else-entirely", {"from": alice.actor_id, "type": "x", "payload": {}})
    delivered = sr._deliver_messages()

    assert delivered == 0
    # Untouched — still there for whichever process actually hosts that actor.
    assert len(pr.peek_actor_inbox("someone-else-entirely")) == 1
