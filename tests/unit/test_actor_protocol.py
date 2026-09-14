"""Tests for ActorProtocol (Step 13.4, ACP-1).

Structural (typing.Protocol) contract every registered actor must satisfy —
replaces the previous hasattr(actor, "tick") duck-typing.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.society.actor_protocol import ActorProtocol
from src.monkey_brain.kernel.compile.cognitive_actor import CognitiveActor


def test_cognitive_actor_satisfies_actor_protocol():
    actor = CognitiveActor(entity_id="proto-test-1")
    assert isinstance(actor, ActorProtocol)


def test_entity_id_property_matches_id():
    actor = CognitiveActor(entity_id="proto-test-2")
    assert actor.entity_id == actor.id == "proto-test-2"


def test_actor_system_satisfies_actor_protocol():
    from src.actor.actor import ActorSystem

    actor = ActorSystem(actor_id="proto-test-3")
    assert isinstance(actor, ActorProtocol)


def test_malformed_actor_does_not_satisfy_protocol():
    class NotAnActor:
        pass

    assert not isinstance(NotAnActor(), ActorProtocol)


@pytest.mark.asyncio
async def test_tick_is_callable_on_protocol_conformant_actor():
    actor = CognitiveActor(entity_id="proto-test-4")
    assert isinstance(actor, ActorProtocol)
    result = await actor.tick()
    assert result is not None
