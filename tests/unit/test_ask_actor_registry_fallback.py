"""AskActorCapability registry-based target resolution — qualification
tests for Deployment Architecture Top 10 item 4 (the last item on that
list): "Replace AskActorCapability's in-process pr.all_societies()/
sr.active_actors() target resolution with a locate_actor() lookup as a
fallback when local iteration misses, so the already-real NATS transport
actually works across process/node boundaries."

Prior state: AskActorCapability.handle() only ever searched
pr.all_societies() (this process's own in-memory registry) for the target
actor. An actor genuinely registered on a DIFFERENT node was reported "no
actor named X found" even though it existed — indistinguishable, from the
asker's point of view, from a genuinely mistyped/nonexistent name.

Fix: when local search misses, fall back to a single, cheap, no-side-
effect PlanetaryRuntime.locate_actor() read (the durable Actor Registry
built earlier in this session's work). If it finds a real registry
record, resolution succeeds with a degraded (name-only, no goals) role
description; the real NATS request still only ever needed target_id as a
plain string, so the actual cross-process message delivery was already
correct once given a real id. If NATS is unavailable AND the target isn't
locally resident, the capability now returns an honest "different node,
no NATS" failure instead of either crashing or silently mis-answering via
the in-process fallback (which requires the target to genuinely be
resident).

Uses the exact call shape proven by the pre-existing
tests/unit/test_communication_verification.py::TestNoRetryOnAskActorTimeout
— this file extends that established pattern rather than inventing a new
one. The eligibility check itself (PlanetaryRuntime.resolve_communication)
is monkeypatched to a known, controlled outcome in the cross-process test
below — deliberately isolating what this fix actually changed (target
resolution) from AffiliationGraph's own, separately-tested default-deny/
allow behavior for two actors with no prior relationship, which is out of
this fix's scope.

Per this repo's session convention, this file is written but not executed
by the assistant. Run with:
    python -m pytest tests/unit/test_ask_actor_registry_fallback.py -v
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
from src.monkey_brain.kernel.society.communication import CommunicationDecision
from src.monkey_brain.kernel.domains.grocery import AskActorCapability


class _FakeActorHashRedis:
    """Minimal in-memory stand-in for the subset of redis-py's API
    PlanetaryRuntime._save_actor()/locate_actor() actually use: a single
    Redis HASH (HSET/HGET/HGETALL), keyed by actor_id. Shared across two
    PlanetaryRuntime instances to simulate "another node registered this
    actor" without spawning a real second process."""

    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}

    def ping(self) -> bool:
        return True

    def hset(self, name, key, value):
        self._hashes.setdefault(name, {})[key] = value

    def hget(self, name, key):
        return self._hashes.get(name, {}).get(key)

    def hgetall(self, name):
        return dict(self._hashes.get(name, {}))

    def hdel(self, name, key):
        self._hashes.get(name, {}).pop(key, None)


def _register(pr: PlanetaryRuntime, name: str, **kwargs):
    return pr.register_actor(
        ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.AI_AGENT)),
        **kwargs,
    )


# ── Regression: a genuinely nonexistent actor still fails honestly ──────


@pytest.mark.asyncio
async def test_genuinely_unknown_actor_still_reports_not_found_not_remote():
    """Confirms the fix didn't make EVERY resolution failure look like "a
    different node" — a truly nonexistent actor_id (never registered
    anywhere, not even in the registry) still gets the original, correct
    "no actor named" error. Mirrors test_communication_verification.py's
    own TestNoRetryOnAskActorTimeout, extended to also assert against the
    new registry fallback explicitly finding nothing."""
    pr = PlanetaryRuntime()
    club = pr.create_society("AskActor Registry Test A", society_type="community")
    alice = _register(pr, "Alice", society_id=club.society.society_id)

    assert pr.locate_actor("Nobody Real") is None  # the new fallback correctly finds nothing either

    result = await AskActorCapability().handle(
        {
            "context": {
                "planetary_runtime": pr,
                "actor_id": alice.actor_id,
                "actor_role": "Alice",
            },
            "parameters": {"target_actor": "Nobody Real", "question": "hi?"},
        }
    )
    assert result["success"] is False
    assert "no actor named" in result["error"]


# ── The actual fix: target resolvable via registry, not local residency ──


@pytest.mark.asyncio
async def test_ask_actor_resolves_target_via_registry_when_not_locally_resident():
    """The literal cross-process scenario: Carol is registered on pr2
    ("a different node"), never on pr1. Alice (on pr1) asks for Carol by
    her real actor_id. Before this fix, pr1's local-only search would
    report "no actor named <carol's uuid> found" even though Carol
    genuinely exists. After this fix, pr1.locate_actor() finds her via
    the shared registry, resolution succeeds, and — since this test
    environment has no real NATS broker and Carol isn't locally resident
    — the capability reports the new, honest "different node, no NATS"
    failure instead, which is the CORRECT terminal state for this
    scenario (not a resolution failure)."""
    redis = _FakeActorHashRedis()

    pr2 = PlanetaryRuntime()
    pr2._redis = redis
    club2 = pr2.create_society("AskActor Registry Test B (remote)", society_type="community")
    carol = pr2.register_actor(
        ActorProfile(identity=ActorIdentity(name="Carol", actor_type=ActorType.AI_AGENT)),
        society_id=club2.society.society_id,
    )  # register_actor() itself calls _save_actor() internally, writing to the shared fake redis

    pr1 = PlanetaryRuntime()
    pr1._redis = redis
    club1 = pr1.create_society("AskActor Registry Test A (local)", society_type="community")
    alice = _register(pr1, "Alice", society_id=club1.society.society_id)

    # Confirm the low-level mechanism directly: pr1 (which never locally
    # registered Carol) can still find her via the shared registry.
    entry = pr1.locate_actor(carol.actor_id)
    assert entry is not None
    assert entry.name == "Carol"

    # Isolate the fix under test from AffiliationGraph's own, separately-
    # tested default eligibility rules for two actors with no established
    # relationship -- fix resolve_communication to a known ALLOWED
    # decision so this test's outcome depends only on target resolution.
    def _fake_resolve(sender_id, recipient_id, *, correlation_id="", causation_id=""):
        return CommunicationDecision(
            sender_id=sender_id,
            recipient_id=recipient_id,
            allowed=True,
            reason="test override",
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    pr1.resolve_communication = _fake_resolve

    result = await AskActorCapability().handle(
        {
            "context": {
                "planetary_runtime": pr1,
                "actor_id": alice.actor_id,
                "actor_role": "Alice",
            },
            "parameters": {
                "target_actor": carol.actor_id,
                "question": "are you there?",
            },
        }
    )

    assert result["success"] is False
    # The critical assertion: resolution succeeded (this is NOT a "not
    # found" failure) -- it correctly reaches the honest "different node,
    # no NATS available" terminal state instead.
    assert "no actor named" not in result["error"]
    assert "different node" in result["error"]
    assert "no NATS" in result["error"] or "NATS connection" in result["error"]


@pytest.mark.asyncio
async def test_ask_actor_registry_fallback_only_tried_after_local_search_misses():
    """When the target genuinely IS resident locally, the registry
    fallback path must never be reached at all — confirmed by monkey-
    patching locate_actor to raise if called, then verifying the existing
    local-search success path still works and locate_actor was never
    touched."""
    pr = PlanetaryRuntime()
    club = pr.create_society("AskActor Registry Test C", society_type="community")
    alice = _register(pr, "Alice", society_id=club.society.society_id)
    bob = _register(pr, "Bob", society_id=club.society.society_id)

    def _should_not_be_called(actor_id):
        raise AssertionError("locate_actor() must not be called when local search already found the target")

    pr.locate_actor = _should_not_be_called

    def _fake_resolve(sender_id, recipient_id, *, correlation_id="", causation_id=""):
        return CommunicationDecision(
            sender_id=sender_id,
            recipient_id=recipient_id,
            allowed=True,
            reason="test override",
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    pr.resolve_communication = _fake_resolve

    result = await AskActorCapability().handle(
        {
            "context": {
                "planetary_runtime": pr,
                "actor_id": alice.actor_id,
                "actor_role": "Alice",
            },
            "parameters": {"target_actor": bob.actor_id, "question": "hi bob"},
        }
    )
    # No NATS in this test env and bob IS locally resident -> falls to the
    # in-process AnswerQuestionCapability path, which will itself likely
    # fail for unrelated reasons (no real KG/LLM wired here) — this test
    # only cares that locate_actor() was never invoked, i.e. that failure
    # (whatever it is) is NOT a registry-fallback-path failure.
    assert "different node" not in result.get("error", "")
