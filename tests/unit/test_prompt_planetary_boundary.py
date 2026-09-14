from types import SimpleNamespace

import pytest

from src.monkey_brain.api.routes.prompt import unified_prompt
from src.monkey_brain.kernel.geography.entity import (
    GeographicEntity,
    GeographicEntityType,
)
from src.monkey_brain.kernel.geography.registry import GeographicRegistry
from src.monkey_brain.kernel.geography.runtime import GeographicEntityRuntime
from src.monkey_brain.kernel.models import PromptRequest
from src.monkey_brain.kernel.society.context_stream import (
    ContextEvent,
    ContextEventType,
)
from src.monkey_brain.kernel.society.runtime import SocietyRuntime


@pytest.mark.asyncio
async def test_geographic_runtime_forwards_request_to_actor_society():
    registry = GeographicRegistry()
    planet = registry.create(GeographicEntityType.PLANET, "Planet")
    parent = planet
    for entity_type in (
        GeographicEntityType.COUNTRY,
        GeographicEntityType.STATE,
        GeographicEntityType.COUNTY,
        GeographicEntityType.CITY,
        GeographicEntityType.STREET,
        GeographicEntityType.BUILDING,
        GeographicEntityType.SPACE,
    ):
        parent = registry.create(entity_type, entity_type.value, parent_id=parent.entity_id)
    space = parent

    calls = []

    class Society:
        is_active = True

        def active_actors(self):
            # GeographicEntityRuntime.tick()'s no-membership-lookup fallback
            # path does `a.actor_id == actor_id` against these — a bare
            # object() has no such attribute (AttributeError). Match the
            # real ActorRuntimeState shape closely enough for that check.
            return (SimpleNamespace(actor_id="actor-1"),)

        async def tick(self, *, target_actor_id=None, prompt_request=None, exclude_actor_ids=None):
            # GeographicEntityRuntime.tick() calls society_runtime.tick()
            # with exclude_actor_ids too (kernel/geography/runtime.py:331-335)
            # — missing it here raised a TypeError that the real code's own
            # `except Exception: logger.error(...)` (runtime.py:346-347)
            # silently swallowed, so this stub's tick() was never actually
            # reached; the test failed on an empty result with no visible
            # exception anywhere in the assertion output.
            calls.append((target_actor_id, prompt_request))
            return SimpleNamespace(
                actors_ticked=1,
                interactions_routed=0,
                actor_execution_result={"answer": "done"},
            )

    registry.host_society(space.entity_id, "society-1")
    result = await GeographicEntityRuntime(
        registry,
        planet.entity_id,
        lambda society_id: Society(),
    ).tick(actor_id="actor-1", prompt_request={"question": "hello"})

    assert result.actor_execution_result == {"answer": "done"}
    assert calls == [("actor-1", {"question": "hello"})]


@pytest.mark.asyncio
async def test_prompt_route_delegates_to_planetary_runtime(monkeypatch):
    # This test is about routing/delegation, not world validation — the
    # minimal Planetary stub below deliberately doesn't implement the
    # real PlanetaryRuntime's geo_registry/all_societies/
    # membership_registry interface world_validator.py needs. Bypass the
    # gate the same way every live server session this build used did
    # (WORLD_VALIDATION_GATE_EXECUTE=false), rather than bloat the stub
    # into a second, fake PlanetaryRuntime just to satisfy it.
    monkeypatch.setenv("WORLD_VALIDATION_GATE_EXECUTE", "false")
    calls = []

    class Planetary:
        def restore_actor_belief(self, actor_id):
            # No-op: prompt.py calls this and checkpoint_actor_belief
            # below (for their side effects, before/after
            # execute_actor_request) unconditionally on the real
            # PlanetaryRuntime interface -- these stubs only need to be
            # present, not do anything, for this routing/delegation test.
            pass

        def checkpoint_actor_belief(self, actor_id):
            pass

        async def execute_actor_request(self, actor_id, prompt_request):
            calls.append((actor_id, prompt_request.question))
            return {"actions": [], "actual_outcome": {"goal_achieved": True}}

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(planetary_runtime=Planetary())))
    response = await unified_prompt(
        request,
        PromptRequest(question="hello"),
        None,
        "actor-1",
    )

    assert calls == [("actor-1", "hello")]
    assert response.query_result["actor_id"] == "actor-1"
    assert response.business_flow["result"]["goal_achieved"] is True


def test_active_society_stream_delivers_every_event_to_actor_runtime():
    society_runtime = SocietyRuntime()
    received = []

    society_runtime._actors["actor-1"] = SimpleNamespace(
        actor_id="actor-1",
        actor_runtime=SimpleNamespace(receive_context_event=received.append),
    )

    event = ContextEvent(
        event_type=ContextEventType.PREDICTION,
        actor_id="actor-1",
        description="prediction",
    )
    society_runtime.context_stream.publish(event)

    assert len(received) == 1
    assert received[0].event_type is ContextEventType.PREDICTION
    assert received[0].actor_id == "actor-1"
