"""Tests for Step 14 — Architecture Consolidation: canonical belief
persistence across /prompt requests.

Covers PlanetaryRuntime.restore_actor_belief()/checkpoint_actor_belief(),
which persist kernel/pipeline/belief_state.py::BeliefState (the
representation CognitiveRuntime.tick() actually reads/writes) via
ActorStateStore, replacing this session's earlier local-JSON scheme that
targeted the vestigial SparseTransitionTensor instead.
"""

from __future__ import annotations

from unittest.mock import patch

from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
)
from src.monkey_brain.persistence.actor_state_store import PersistedActorState


def _register(pr: PlanetaryRuntime, name: str = "Alice"):
    return pr.register_actor(ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN)))


class _FakeActorStateStore:
    """In-memory stand-in for ActorStateStore — no real MongoDB needed to
    exercise restore_actor_belief()/checkpoint_actor_belief()'s own logic."""

    def __init__(self, fail: bool = False):
        self._docs: dict[tuple[str, str], PersistedActorState] = {}
        self._fail = fail

    def load(self, actor_id: str, tenant_id: str):
        if self._fail:
            raise RuntimeError("simulated Mongo outage")
        return self._docs.get((tenant_id, actor_id))

    def save(self, state: PersistedActorState) -> None:
        if self._fail:
            raise RuntimeError("simulated Mongo outage")
        self._docs[(state.tenant_id, state.actor_id)] = state


class TestRestoreActorBeliefNoCheckpoint:
    def test_no_checkpoint_is_noop_returns_false(self):
        pr = PlanetaryRuntime()
        state = _register(pr)
        with patch.object(pr, "_get_actor_state_store", return_value=_FakeActorStateStore()):
            assert pr.restore_actor_belief(state.actor_id) is False

    def test_unknown_actor_returns_false(self):
        pr = PlanetaryRuntime()
        assert pr.restore_actor_belief("does-not-exist") is False


class TestCheckpointRestoreRoundTrip:
    def test_round_trip_reproduces_belief_content(self):
        pr = PlanetaryRuntime()
        state = _register(pr)
        actor = state.actor
        belief = actor.pipeline_belief()
        belief.add_fact(entity="milk", attribute="quantity", value="2L")
        belief.update_intent(intent_type="shopping", confidence=0.9)

        store = _FakeActorStateStore()
        with patch.object(pr, "_get_actor_state_store", return_value=store):
            pr.checkpoint_actor_belief(state.actor_id)

            # Simulate a restart: wipe the actor's in-memory belief.
            fresh_cls = type(belief)
            actor.restore_pipeline_belief(fresh_cls(actor_id=belief.actor_id, tenant_id=belief.tenant_id))
            assert actor.pipeline_belief().facts == []

            restored = pr.restore_actor_belief(state.actor_id)
            assert restored is True

        assert len(actor.pipeline_belief().facts) == 1
        assert actor.pipeline_belief().facts[0].value == "2L"
        assert actor.pipeline_belief().intent.type == "shopping"


class TestFailSoftBehavior:
    def test_checkpoint_failure_does_not_raise(self):
        pr = PlanetaryRuntime()
        state = _register(pr)
        with patch.object(pr, "_get_actor_state_store", return_value=_FakeActorStateStore(fail=True)):
            pr.checkpoint_actor_belief(state.actor_id)  # must not raise

    def test_restore_failure_does_not_raise_returns_false(self):
        pr = PlanetaryRuntime()
        state = _register(pr)
        with patch.object(pr, "_get_actor_state_store", return_value=_FakeActorStateStore(fail=True)):
            assert pr.restore_actor_belief(state.actor_id) is False

    def test_mongo_construction_failure_degrades_gracefully(self):
        """_get_actor_state_store() itself must never raise — a genuinely
        unreachable Mongo (e.g. get_db_pool() raising) must degrade to
        'continue with in-memory belief', not fail the request."""
        pr = PlanetaryRuntime()
        state = _register(pr)
        with patch(
            "src.monkey_brain.persistence.db_pool.get_db_pool",
            side_effect=RuntimeError("no mongo"),
        ):
            assert pr.restore_actor_belief(state.actor_id) is False
            pr.checkpoint_actor_belief(state.actor_id)  # must not raise
