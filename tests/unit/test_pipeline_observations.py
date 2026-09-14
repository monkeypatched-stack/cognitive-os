"""Tests for Observation & Belief Fusion.

Validates observation model, provider, fusion rules, and runtime integration.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineRequest,
    CompiledRequest,
    RuntimeContext,
)
from src.monkey_brain.kernel.pipeline.observations import (
    Observation,
    ObservationSet,
    ObservationProvider,
    WorldPollingProvider,
    Provenance,
    BeliefFusion,
)
from src.monkey_brain.kernel.pipeline.belief_state import BeliefState
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime
from src.monkey_brain.kernel.pipeline.actor import Actor

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_compiled(question: str = "get 2 l of milk") -> CompiledRequest:
    request = PipelineRequest(question=question, actor_id="user-1", tenant_id="acme")
    ctx = MagicMock()
    ctx.run_id = "run-001"
    return CompiledRequest(
        request=request,
        intent={"intent": "shopping", "confidence": 0.9},
        goal={"name": "acquire_item", "description": "Get milk"},
        intent_ir=MagicMock(),
        goal_ir=MagicMock(goal="get 2 l of milk"),
        execution_context=ctx,
    )


def _make_context() -> RuntimeContext:
    world = MagicMock()
    world.states.return_value = ["start", "middle", "end"]
    world.domains.return_value = ["task"]
    world.nnz.return_value = 5
    return RuntimeContext(world=world, actor=MagicMock())


def _make_obs(
    entity: str = "milk",
    attribute: str = "quantity",
    value: Any = "2L",
    confidence: float = 0.9,
    source: str = "sensor",
) -> Observation:
    return Observation(
        entity=entity,
        attribute=attribute,
        value=value,
        confidence=confidence,
        provenance=Provenance(source=source, method="direct", reliability=0.9),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Observation Model
# ═══════════════════════════════════════════════════════════════════════════


class TestObservation:
    def test_construction(self):
        obs = _make_obs()
        assert obs.entity == "milk"
        assert obs.attribute == "quantity"
        assert obs.value == "2L"
        assert obs.confidence == 0.9
        assert obs.provenance.source == "sensor"

    def test_immutable(self):
        from dataclasses import FrozenInstanceError

        obs = _make_obs()
        with pytest.raises(FrozenInstanceError):
            obs.entity = "bread"

    def test_provenance_fields(self):
        p = Provenance(source="camera", method="detection", reliability=0.85)
        assert p.source == "camera"
        assert p.reliability == 0.85

    def test_provenance_immutable(self):
        from dataclasses import FrozenInstanceError

        p = Provenance(source="x")
        with pytest.raises(FrozenInstanceError):
            p.source = "y"


# ═══════════════════════════════════════════════════════════════════════════
# ObservationSet
# ═══════════════════════════════════════════════════════════════════════════


class TestObservationSet:
    def test_empty(self):
        s = ObservationSet()
        assert s.is_empty()
        assert len(s.observations) == 0

    def test_with_observations(self):
        obs1 = _make_obs(entity="milk", source="sensor")
        obs2 = _make_obs(entity="bread", source="camera")
        s = ObservationSet(observations=(obs1, obs2), actor_id="a1")
        assert not s.is_empty()
        assert len(s.observations) == 2
        assert s.actor_id == "a1"

    def test_by_source(self):
        obs1 = _make_obs(source="sensor")
        obs2 = _make_obs(source="sensor")
        obs3 = _make_obs(source="camera")
        s = ObservationSet(observations=(obs1, obs2, obs3))
        by_src = s.by_source()
        assert len(by_src["sensor"]) == 2
        assert len(by_src["camera"]) == 1

    def test_by_entity(self):
        obs1 = _make_obs(entity="milk")
        obs2 = _make_obs(entity="milk")
        obs3 = _make_obs(entity="bread")
        s = ObservationSet(observations=(obs1, obs2, obs3))
        by_ent = s.by_entity()
        assert len(by_ent["milk"]) == 2
        assert len(by_ent["bread"]) == 1

    def test_high_confidence(self):
        obs1 = _make_obs(confidence=0.9)
        obs2 = _make_obs(confidence=0.3)
        s = ObservationSet(observations=(obs1, obs2))
        hc = s.high_confidence(threshold=0.7)
        assert len(hc) == 1
        assert hc[0].confidence == 0.9

    def test_immutable(self):
        from dataclasses import FrozenInstanceError

        s = ObservationSet(observations=(_make_obs(),))
        with pytest.raises(FrozenInstanceError):
            s.observations = ()


# ═══════════════════════════════════════════════════════════════════════════
# WorldPollingProvider
# ═══════════════════════════════════════════════════════════════════════════


class TestWorldPollingProvider:
    def test_observes_world(self):
        provider = WorldPollingProvider()
        world = MagicMock()
        world.states.return_value = ["a", "b", "c"]
        world.domains.return_value = ["task"]
        world.nnz.return_value = 10

        obs_set = provider.observe("actor-1", world)
        assert not obs_set.is_empty()
        assert obs_set.actor_id == "actor-1"
        assert len(obs_set.observations) >= 2  # state_count + transition_count at minimum

    def test_null_world(self):
        provider = WorldPollingProvider()
        obs_set = provider.observe("actor-1", None)
        assert obs_set.is_empty()

    def test_observations_have_provenance(self):
        provider = WorldPollingProvider()
        world = MagicMock()
        world.states.return_value = ["a"]
        obs_set = provider.observe("actor-1", world)
        for obs in obs_set.observations:
            assert obs.provenance.source == "world_polling"


# ═══════════════════════════════════════════════════════════════════════════
# BeliefFusion
# ═══════════════════════════════════════════════════════════════════════════


class TestBeliefFusion:
    def test_new_observation_creates_fact(self):
        fusion = BeliefFusion()
        belief = BeliefState()
        obs = _make_obs(entity="milk", attribute="qty", value="2L")

        fusion.update(belief, ObservationSet(observations=(obs,)))

        assert len(belief.facts) == 1
        assert belief.facts[0].entity == "milk"
        assert belief.facts[0].value == "2L"

    def test_identical_observation_increases_confidence(self):
        fusion = BeliefFusion()
        belief = BeliefState()
        obs1 = _make_obs(confidence=0.6)
        obs2 = _make_obs(confidence=0.8)

        fusion.update(belief, ObservationSet(observations=(obs1,)))
        fusion.update(belief, ObservationSet(observations=(obs2,)))

        # Should have 1 fact with blended confidence
        assert len(belief.facts) == 1
        assert belief.facts[0].confidence == 0.7  # (0.6 + 0.8) / 2

    def test_higher_confidence_replaces_lower(self):
        fusion = BeliefFusion()
        belief = BeliefState()
        obs_low = _make_obs(value="1L", confidence=0.3)
        obs_high = _make_obs(value="2L", confidence=0.9)

        fusion.update(belief, ObservationSet(observations=(obs_low,)))
        fusion.update(belief, ObservationSet(observations=(obs_high,)))

        assert len(belief.facts) == 1
        assert belief.facts[0].value == "2L"
        assert belief.facts[0].confidence == 0.9

    def test_lower_confidence_does_not_overwrite(self):
        fusion = BeliefFusion()
        belief = BeliefState()
        obs_high = _make_obs(value="2L", confidence=0.9)
        obs_low = _make_obs(value="1L", confidence=0.3)

        fusion.update(belief, ObservationSet(observations=(obs_high,)))
        fusion.update(belief, ObservationSet(observations=(obs_low,)))

        assert len(belief.facts) == 1
        assert belief.facts[0].value == "2L"
        assert belief.facts[0].confidence == 0.9

    def test_conflicting_observation_creates_hypothesis(self):
        fusion = BeliefFusion()
        belief = BeliefState()
        obs1 = _make_obs(value="2L", confidence=0.9)
        obs2 = _make_obs(value="1L", confidence=0.9)  # same confidence, different value

        fusion.update(belief, ObservationSet(observations=(obs1,)))
        fusion.update(belief, ObservationSet(observations=(obs2,)))

        assert len(belief.hypotheses) == 1
        assert "Conflict" in belief.hypotheses[0].claim

    def test_provenance_preserved(self):
        fusion = BeliefFusion()
        belief = BeliefState()
        obs = _make_obs(source="camera")
        fusion.update(belief, ObservationSet(observations=(obs,)))
        assert belief.facts[0].source == "camera"

    def test_multiple_observations(self):
        fusion = BeliefFusion()
        belief = BeliefState()
        obs1 = _make_obs(entity="milk", attribute="qty", value="2L")
        obs2 = _make_obs(entity="bread", attribute="qty", value="1")
        obs3 = _make_obs(entity="eggs", attribute="qty", value="12")

        fusion.update(belief, ObservationSet(observations=(obs1, obs2, obs3)))
        assert len(belief.facts) == 3

    def test_empty_observations(self):
        fusion = BeliefFusion()
        belief = BeliefState()
        fusion.update(belief, ObservationSet())
        assert len(belief.facts) == 0

    def test_observations_recorded(self):
        fusion = BeliefFusion()
        belief = BeliefState()
        obs = _make_obs()
        fusion.update(belief, ObservationSet(observations=(obs,)))
        assert len(belief.observations) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Runtime Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestRuntimeIntegration:
    @pytest.mark.asyncio
    async def test_observe_uses_provider(self):
        """Observe should use ObservationProvider, not construct observations."""

        class MockProvider:
            def observe(self, actor_id, world):
                return ObservationSet(
                    observations=(
                        Observation(
                            entity="milk",
                            attribute="qty",
                            value="2L",
                            confidence=0.9,
                            provenance=Provenance(source="mock"),
                        ),
                    ),
                    actor_id=actor_id,
                )

        rt = CognitiveRuntime(observation_provider=MockProvider())
        result = await rt.run(_make_compiled(), _make_context())

        # Facts should come from the mock provider via BeliefFusion
        assert result["belief"]["facts"] >= 1

    @pytest.mark.asyncio
    async def test_belief_fusion_updates_belief(self):
        """BeliefFusion should update belief state from observations."""
        rt = CognitiveRuntime()
        result = await rt.run(_make_compiled(), _make_context())

        # WorldPollingProvider produces observations → BeliefFusion creates facts
        assert result["belief"]["observations"] >= 1
        assert result["belief"]["facts"] >= 1

    @pytest.mark.asyncio
    async def test_custom_fusion_engine(self):
        """A custom BeliefFusion can be injected."""

        class CustomFusion:
            def update(self, belief, observations):
                belief.add_fact(entity="custom", attribute="injected", value=True)
                return belief

        rt = CognitiveRuntime(belief_fusion=CustomFusion())
        result = await rt.run(_make_compiled(), _make_context())

        assert result["belief"]["facts"] >= 1

    @pytest.mark.asyncio
    async def test_fusion_rules_applied(self):
        """Verify deterministic fusion rules are applied."""
        fusion = BeliefFusion()
        belief = BeliefState()

        # First observation
        obs1 = _make_obs(value="2L", confidence=0.5)
        fusion.update(belief, ObservationSet(observations=(obs1,)))
        assert len(belief.facts) == 1
        assert belief.facts[0].confidence == 0.5

        # Same value, higher confidence → blend
        obs2 = _make_obs(value="2L", confidence=0.9)
        fusion.update(belief, ObservationSet(observations=(obs2,)))
        assert len(belief.facts) == 1
        assert belief.facts[0].confidence == 0.7  # blended

        # Different value, higher confidence → replace
        obs3 = _make_obs(value="3L", confidence=0.95)
        fusion.update(belief, ObservationSet(observations=(obs3,)))
        assert len(belief.facts) == 1
        assert belief.facts[0].value == "3L"
        assert belief.facts[0].confidence == 0.95

        # Different value, same confidence → hypothesis
        obs4 = _make_obs(value="1L", confidence=0.95)
        fusion.update(belief, ObservationSet(observations=(obs4,)))
        assert len(belief.hypotheses) >= 1
        assert "Conflict" in belief.hypotheses[0].claim
