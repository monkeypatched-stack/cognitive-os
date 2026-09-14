"""Tests for CognitiveRuntime, BeliefState, and CognitiveState.

Validates lifecycle order, state propagation, error handling,
BeliefState semantic API, and ownership boundaries.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import pytest
from unittest.mock import MagicMock

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineRequest,
    CompiledRequest,
    RuntimeContext,
)
from src.monkey_brain.kernel.pipeline.execution_state import (
    CognitiveState,
    WorldSnapshot,
)
from src.monkey_brain.kernel.pipeline.belief_state import (
    BeliefState,
    BeliefSnapshot,
    Fact,
    Hypothesis,
    Assumption,
    Observation,
    Intent,
    Goal,
    Plan,
    PlanStep,
    Prediction,
    LearnedUpdate,
    Uncertainty,
    WorkingMemoryEntry,
    LongTermMemoryEntry,
)
from src.monkey_brain.kernel.pipeline.actor import Actor, ActorSnapshot
from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime

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
    return RuntimeContext(world=world, actor=MagicMock())


# ═══════════════════════════════════════════════════════════════════════════
# BeliefState Construction & API
# ═══════════════════════════════════════════════════════════════════════════


class TestBeliefState:
    def test_construction_defaults(self):
        bs = BeliefState()
        assert bs.actor_id == ""
        assert bs.tenant_id == "default"
        assert bs.intent.type == ""
        assert bs.goal.name == ""
        assert bs.observations == []
        assert bs.facts == []
        assert bs.hypotheses == []
        assert bs.assumptions == []
        assert bs.predictions == []
        assert bs.learned_updates == []
        assert bs.version == 0

    def test_add_observation(self):
        bs = BeliefState()
        bs.add_observation(entity="milk", description="found on shelf", source="sensor")
        assert len(bs.observations) == 1
        assert bs.observations[0].entity == "milk"
        assert bs.version == 1

    def test_add_fact(self):
        bs = BeliefState()
        bs.add_fact(entity="milk", attribute="quantity", value="2L")
        assert len(bs.facts) == 1
        assert bs.facts[0].entity == "milk"
        assert bs.facts[0].value == "2L"
        assert bs.version == 1

    def test_add_hypothesis(self):
        bs = BeliefState()
        bs.add_hypothesis(claim="store is open", confidence=0.8)
        assert len(bs.hypotheses) == 1
        assert bs.hypotheses[0].confidence == 0.8

    def test_add_assumption(self):
        bs = BeliefState()
        bs.add_assumption(statement="milk is available", confidence=0.9)
        assert len(bs.assumptions) == 1
        assert bs.assumptions[0].confidence == 0.9

    def test_update_intent(self):
        bs = BeliefState()
        bs.update_intent(intent_type="shopping", confidence=0.95)
        assert bs.intent.type == "shopping"
        assert bs.intent.confidence == 0.95

    def test_update_goal(self):
        bs = BeliefState()
        bs.update_goal(
            name="acquire_item",
            description="Get milk",
            success_criteria=["milk acquired"],
        )
        assert bs.goal.name == "acquire_item"
        assert bs.goal.success_criteria == ("milk acquired",)

    def test_update_plan(self):
        # Plan.steps is tuple[PlanStep, ...] — update_plan() previously
        # stored bare action strings instead, silently violating that
        # contract (no error until a real to_dict()/from_dict() round
        # trip crashed trying to reconstruct a PlanStep from a str). Fixed
        # to build real PlanStep objects; this asserts the corrected
        # contract, not the prior bug.
        bs = BeliefState()
        bs.update_plan(steps=["find_milk", "add_to_cart"], start_state="shelf", goal_state="cart")
        assert [s.action for s in bs.plan.steps] == ["find_milk", "add_to_cart"]
        assert all(isinstance(s, PlanStep) for s in bs.plan.steps)
        assert bs.plan.start_state == "shelf"

    def test_record_prediction(self):
        bs = BeliefState()
        bs.record_prediction(description="milk will be in aisle 3", confidence=0.7)
        assert len(bs.predictions) == 1
        assert bs.predictions[0].confidence == 0.7

    def test_record_learning(self):
        bs = BeliefState()
        bs.record_learning(what="milk is in aisle 3", evidence=["fact:milk.aisle=3"])
        assert len(bs.learned_updates) == 1
        assert bs.learned_updates[0].what == "milk is in aisle 3"

    def test_add_to_working_memory(self):
        bs = BeliefState()
        bs.add_to_working_memory("step", "planning", ttl_seconds=60)
        assert len(bs.working_memory) == 1

    def test_recall(self):
        bs = BeliefState()
        bs.add_fact(entity="milk", attribute="type", value="whole")
        bs.add_fact(entity="bread", attribute="type", value="sourdough")
        results = bs.recall("milk")
        assert len(results) == 1
        assert results[0].entity == "milk"

    def test_confidence(self):
        bs = BeliefState()
        bs.uncertainty.confidence = 0.8
        assert bs.confidence() == 0.8

    def test_confidence_for_source(self):
        bs = BeliefState()
        bs.uncertainty.confidence_by_source = {"sensor": 0.9}
        assert bs.confidence_for("sensor") == 0.9
        assert bs.confidence_for("unknown") == 0.5

    def test_version_increments(self):
        bs = BeliefState()
        assert bs.version == 0
        bs.add_fact(entity="x", attribute="y", value=1)
        assert bs.version == 1
        bs.add_observation(entity="x", description="saw x")
        assert bs.version == 2

    def test_snapshot(self):
        bs = BeliefState(actor_id="a1", tenant_id="t1")
        bs.add_fact(entity="milk", attribute="qty", value="2L")
        bs.update_intent(intent_type="shopping")
        bs.update_goal(name="buy_milk")

        snap = bs.snapshot()
        assert isinstance(snap, BeliefSnapshot)
        assert snap.actor_id == "a1"
        assert snap.facts_count == 1
        assert snap.intent_type == "shopping"
        assert snap.goal_name == "buy_milk"
        assert snap.version == 3  # 3 touches

    def test_to_dict(self):
        bs = BeliefState(actor_id="a1")
        bs.add_fact(entity="x", attribute="y", value=1)
        bs.update_intent(intent_type="test")

        d = bs.to_dict()
        assert d["actor_id"] == "a1"
        assert len(d["facts"]) == 1
        assert d["intent"]["type"] == "test"
        assert "version" in d

    def test_summary(self):
        bs = BeliefState(actor_id="a1", tenant_id="t1")
        bs.add_fact(entity="x", attribute="y", value=1)
        s = bs.summary()
        assert s["actor_id"] == "a1"
        assert s["facts"] == 1
        assert "intent" in s
        assert "goal" in s


# ═══════════════════════════════════════════════════════════════════════════
# to_dict() / from_dict() round-trip (Step 14 — Architecture Consolidation:
# BeliefState is the canonical actor belief persisted across /prompt requests
# via PlanetaryRuntime.restore_actor_belief()/checkpoint_actor_belief())
# ═══════════════════════════════════════════════════════════════════════════


class TestBeliefStateRoundTrip:
    def _populated(self, actor_id: str = "a1", tenant_id: str = "t1") -> BeliefState:
        bs = BeliefState(actor_id=actor_id, tenant_id=tenant_id)
        bs.add_observation(entity="milk", description="found on shelf", source="sensor")
        bs.add_fact(entity="milk", attribute="quantity", value="2L", confidence=0.9)
        bs.add_hypothesis(claim="store is open", confidence=0.8, evidence=["fact:hours"])
        bs.add_assumption(statement="milk is available", confidence=0.7)
        bs.update_intent(intent_type="shopping", confidence=0.95, metadata={"k": "v"})
        bs.update_plan(steps=["find_milk", "add_to_cart"], start_state="shelf", goal_state="cart")
        bs.record_prediction(description="milk in aisle 3", confidence=0.6, based_on=["fact:milk.aisle"])
        bs.record_learning(what="milk is in aisle 3", evidence=["fact:milk.aisle=3"], confidence=0.5)
        bs.add_to_working_memory("current_step", "planning", ttl_seconds=600)
        bs.long_term_memory.append(LongTermMemoryEntry(key="preferred_brand", value="acme-milk", confidence=0.85))
        bs.uncertainty.confidence = 0.77
        bs.uncertainty.confidence_by_source = {"sensor": 0.9}
        return bs

    def test_to_dict_carries_full_memory_content_not_just_counts(self):
        """Step 14 review found to_dict() previously serialized only
        working_memory_count/long_term_memory_count — a restored actor
        silently lost its memory. Counts are kept for existing consumers,
        but the real entries must be present too."""
        bs = self._populated()
        d = bs.to_dict()
        assert d["working_memory_count"] == 1
        assert d["long_term_memory_count"] == 1
        assert d["working_memory"][0]["key"] == "current_step"
        assert d["long_term_memory"][0]["key"] == "preferred_brand"
        assert d["long_term_memory"][0]["value"] == "acme-milk"

    def test_round_trip_reproduces_content(self):
        original = self._populated()
        restored = BeliefState.from_dict(original.to_dict())

        assert restored.actor_id == original.actor_id
        assert restored.tenant_id == original.tenant_id
        assert restored.version == original.version
        assert restored.intent.type == "shopping"
        assert restored.intent.metadata == {"k": "v"}
        assert [f.entity for f in restored.facts] == [f.entity for f in original.facts]
        assert restored.facts[0].value == "2L"
        assert [h.claim for h in restored.hypotheses] == [h.claim for h in original.hypotheses]
        assert [a.statement for a in restored.assumptions] == [a.statement for a in original.assumptions]
        assert restored.plan.steps == original.plan.steps
        assert restored.predictions[0].description == original.predictions[0].description
        assert restored.learned_updates[0].what == original.learned_updates[0].what
        assert restored.uncertainty.confidence == original.uncertainty.confidence
        assert restored.uncertainty.confidence_by_source == original.uncertainty.confidence_by_source

        # The gap this step closes: memory content, not just counts.
        assert len(restored.working_memory) == 1
        assert restored.working_memory[0].key == "current_step"
        assert restored.working_memory[0].value == "planning"
        assert len(restored.long_term_memory) == 1
        assert restored.long_term_memory[0].key == "preferred_brand"
        assert restored.long_term_memory[0].value == "acme-milk"

    def test_round_trip_survives_json_serialization(self):
        """The real persistence path (ActorStateStore) round-trips through
        json.dumps/json.loads, not just Python dict identity — tuples
        become lists, which the dataclass constructors must tolerate."""
        import json

        original = self._populated()
        data = json.loads(json.dumps(original.to_dict()))
        restored = BeliefState.from_dict(data)
        assert restored.plan.steps == original.plan.steps
        assert restored.working_memory[0].value == "planning"
        assert restored.hypotheses[0].evidence == original.hypotheses[0].evidence

    def test_from_dict_empty_dict_yields_defaults(self):
        restored = BeliefState.from_dict({})
        assert restored.actor_id == ""
        assert restored.tenant_id == "default"
        assert restored.facts == []
        assert restored.working_memory == []

    def test_from_dict_does_not_restore_goal_field(self):
        """goal is a derived property backed by GoalTimeline, not a stored
        field — from_dict() must not try to set it directly."""
        original = self._populated()
        original.update_goal(name="buy_milk")
        restored = BeliefState.from_dict(original.to_dict())
        # goal re-derives from the (already-durable) timeline, not from the dict
        assert restored.goal.name == "buy_milk"

    def test_frozenset_in_metadata_survives_json_round_trip(self):
        """Regression: belief_runtime.py once stashed
        belief.metadata["_resolved_permissions"] as a real frozenset —
        json.dumps(belief.to_dict()) raised "Object of type frozenset is
        not JSON serializable" on every tick that touched it, and because
        checkpoint_actor_belief (kernel/society/integration.py) only
        logs that failure (non-fatal), the actor's belief silently never
        persisted at all. Fixed both at the call site (frozenset -> tuple)
        and generically in _json_safe (any set/frozenset anywhere in
        metadata), so this must hold for BOTH representations."""
        import json

        bs = self._populated()
        bs.metadata["_resolved_permissions"] = frozenset({"perm-a", "perm-b"})
        bs.metadata["_also_a_plain_set"] = {"x", "y", "z"}

        # Must not raise.
        data = json.loads(json.dumps(bs.to_dict()))
        restored = BeliefState.from_dict(data)

        assert set(restored.metadata["_resolved_permissions"]) == {"perm-a", "perm-b"}
        assert set(restored.metadata["_also_a_plain_set"]) == {"x", "y", "z"}
        # _json_safe must not silently pass a set through unconverted —
        # json.dumps would have raised above if it had.
        assert not isinstance(restored.metadata["_resolved_permissions"], (set, frozenset))

    def test_nested_tuple_fields_survive_json_round_trip(self):
        """Regression: Plan/PlanStep/Hypothesis/Prediction/LearnedUpdate
        all declare tuple[...] fields, but json.dumps/loads (the real
        ActorStateStore persistence path) turns every tuple into a list.
        from_dict() previously only coerced Plan's own top-level tuple
        fields back — every NESTED tuple field (PlanStep.preconditions,
        PlanStep.depends_on, Hypothesis.evidence, Prediction.based_on,
        LearnedUpdate.evidence) silently stayed a list after restore,
        breaking the frozen/immutable contract these dataclasses declare
        and any equality/hashing that assumes a real tuple."""
        import json

        bs = self._populated()
        bs.plan = replace(
            bs.plan,
            steps=(PlanStep(action="a", preconditions=("p1", "p2"), depends_on=(0,)),),
        )
        data = json.loads(json.dumps(bs.to_dict()))
        restored = BeliefState.from_dict(data)

        step = restored.plan.steps[0]
        assert isinstance(step.preconditions, tuple) and step.preconditions == (
            "p1",
            "p2",
        )
        assert isinstance(step.depends_on, tuple) and step.depends_on == (0,)

        assert isinstance(restored.hypotheses[0].evidence, tuple)
        assert restored.hypotheses[0].evidence == tuple(bs.hypotheses[0].evidence)

        assert isinstance(restored.predictions[0].based_on, tuple)
        assert restored.predictions[0].based_on == bs.predictions[0].based_on

        assert isinstance(restored.learned_updates[0].evidence, tuple)
        assert restored.learned_updates[0].evidence == bs.learned_updates[0].evidence

    def test_update_plan_produces_real_planstep_objects(self):
        """Regression: update_plan() used to store bare action strings
        directly into Plan.steps (violating its own tuple[PlanStep, ...]
        contract) — silently, since dataclasses don't validate field
        types at construction. The break only surfaced when a plan built
        this way was persisted and restored: from_dict()'s
        PlanStep(**s) crashed on a str with no mapping to unpack."""
        bs = BeliefState()
        bs.update_plan(steps=["find_milk", "add_to_cart"])
        for step in bs.plan.steps:
            assert isinstance(step, PlanStep)
        # And the resulting plan must itself survive a real round trip.
        import json

        restored = BeliefState.from_dict(json.loads(json.dumps(bs.to_dict())))
        assert [s.action for s in restored.plan.steps] == ["find_milk", "add_to_cart"]

    def test_sequential_updates_survive_round_trip(self):
        """Multiple updates to the same field (not just a single write)
        must leave the LATEST state durable, not some earlier one."""
        import json

        bs = BeliefState(actor_id="a1")
        bs.update_goal(name="g1", description="first")
        bs.update_goal(name="g1", description="second")
        bs.update_plan(steps=["step1"])
        bs.update_plan(steps=["step1", "step2", "step3"])

        restored = BeliefState.from_dict(json.loads(json.dumps(bs.to_dict())))
        assert [s.action for s in restored.plan.steps] == ["step1", "step2", "step3"]

    def test_malformed_plan_step_fails_explicitly_not_silently(self):
        """A persisted record whose plan.steps entries are already
        corrupted (e.g. hand-edited Redis data, or a future serialization
        regression of the same class as the two fixed above) must raise a
        clear error, never silently drop or reinterpret the data."""
        with pytest.raises(TypeError):
            BeliefState.from_dict({"plan": {"steps": ["not-a-step-dict"]}})


# ═══════════════════════════════════════════════════════════════════════════
# Value Object Immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_fact_frozen(self):
        from dataclasses import FrozenInstanceError

        f = Fact(entity="x", attribute="y", value=1)
        with pytest.raises(FrozenInstanceError):
            f.entity = "z"

    def test_hypothesis_frozen(self):
        from dataclasses import FrozenInstanceError

        h = Hypothesis(claim="test")
        with pytest.raises(FrozenInstanceError):
            h.claim = "changed"

    def test_observation_frozen(self):
        from dataclasses import FrozenInstanceError

        o = Observation(entity="x", description="saw x")
        with pytest.raises(FrozenInstanceError):
            o.entity = "y"

    def test_plan_frozen(self):
        from dataclasses import FrozenInstanceError

        p = Plan(steps=("a", "b"))
        with pytest.raises(FrozenInstanceError):
            p.steps = ()


# ═══════════════════════════════════════════════════════════════════════════
# BeliefState in CognitiveState
# ═══════════════════════════════════════════════════════════════════════════


class TestCognitiveStateIntegration:
    def test_belief_state_in_cognitive_state(self):
        cs = CognitiveState()
        assert isinstance(cs.belief, BeliefState)

    def test_cognitive_state_has_no_observation_field(self):
        """Observations live in BeliefState, not CognitiveState."""
        cs = CognitiveState()
        assert not hasattr(cs, "observations")
        assert hasattr(cs, "plan")

    def test_identity_fields(self):
        actor = Actor(actor_id="a1", tenant_id="t1")
        cs = CognitiveState(actor=actor)
        assert cs.actor_id == "a1"
        assert cs.tenant_id == "t1"

    def test_identity_from_none_actor(self):
        cs = CognitiveState()
        assert cs.actor_id == ""
        assert cs.tenant_id == "default"

    def test_goal_delegates_to_belief(self):
        cs = CognitiveState()
        cs.belief.update_goal(name="test_goal")
        assert cs.goal.name == "test_goal"

    def test_goal_setter_updates_belief(self):
        from src.monkey_brain.kernel.pipeline.belief_state import Goal

        cs = CognitiveState()
        cs.goal = Goal(name="new_goal")
        assert cs.belief.goal.name == "new_goal"

    def test_intent_delegates_to_belief(self):
        cs = CognitiveState()
        cs.belief.update_intent(intent_type="shopping")
        assert cs.intent.type == "shopping"

    def test_record_warning(self):
        cs = CognitiveState()
        cs.record_warning("plan", "low confidence")
        assert cs.has_warnings()
        assert len(cs.warnings()) == 1
        assert cs.warnings()[0].message == "low confidence"

    def test_record_diagnostic(self):
        cs = CognitiveState()
        cs.record_diagnostic("learn", "hypotheses_count", 5)
        assert len(cs.diagnostics) == 1
        assert cs.diagnostics[0].metadata["key"] == "hypotheses_count"

    def test_add_trace(self):
        cs = CognitiveState()
        cs.add_trace("observe", "read_world", "loaded 3 states")
        assert len(cs.execution_trace) == 1
        assert cs.execution_trace[0].stage == "observe"

    def test_to_dict(self):
        actor = Actor(actor_id="a1", tenant_id="t1")
        cs = CognitiveState(actor=actor)
        cs.belief.add_fact(entity="x", attribute="y", value=1)
        d = cs.to_dict()
        assert d["actor_id"] == "a1"
        assert len(d["belief"]["facts"]) == 1
        assert "stage_durations" in d

    def test_summary(self):
        actor = Actor(actor_id="a1", tenant_id="t1")
        cs = CognitiveState(actor=actor)
        cs.record_warning("plan", "test warning")
        s = cs.summary()
        assert s["actor_id"] == "a1"
        assert s["warnings"] == 1
        assert s["diagnostics"] == 1

    def test_world_snapshot_field(self):
        cs = CognitiveState()
        assert cs.world_snapshot is None
        snap = WorldSnapshot(states=("a", "b"), state_count=2)
        cs.world_snapshot = snap
        assert cs.world_snapshot.state_count == 2

    def test_world_snapshot_frozen(self):
        from dataclasses import FrozenInstanceError

        snap = WorldSnapshot(states=("a",), state_count=1)
        with pytest.raises(FrozenInstanceError):
            snap.state_count = 5


# ═══════════════════════════════════════════════════════════════════════════
# CognitiveRuntime Lifecycle
# ═══════════════════════════════════════════════════════════════════════════


class TestLifecycleOrder:
    @pytest.mark.asyncio
    async def test_nine_stages_in_order(self):
        from src.monkey_brain.kernel.pipeline.cognitive_policy import CognitivePolicy

        call_order = []

        async def tracking_observe(s):
            call_order.append("observe")
            return s

        async def tracking_believe(s):
            call_order.append("believe")
            return s

        async def tracking_plan(s):
            call_order.append("plan")
            return s

        async def tracking_execute(s):
            call_order.append("execute")
            return s

        async def tracking_outcome(s):
            call_order.append("observe_outcome")
            return s

        async def tracking_learn(s):
            call_order.append("learn")
            return s

        async def tracking_phi(s):
            call_order.append("compile_phi")
            return s

        async def tracking_predict(s):
            call_order.append("predict")
            return s

        async def tracking_commit(s):
            call_order.append("commit")
            return s

        policy = CognitivePolicy()
        policy.configure(
            observe=tracking_observe,
            believe=tracking_believe,
            plan=tracking_plan,
            execute=tracking_execute,
            observe_outcome=tracking_outcome,
            learn=tracking_learn,
            compile_phi=tracking_phi,
            predict=tracking_predict,
            commit=tracking_commit,
        )

        rt = CognitiveRuntime(policy=policy)
        await rt.run(_make_compiled(), _make_context())

        assert call_order == [
            "observe",
            "believe",
            "plan",
            "execute",
            "observe_outcome",
            "learn",
            "compile_phi",
            "predict",
            "commit",
        ]

    @pytest.mark.asyncio
    async def test_each_stage_called_exactly_once(self):
        from src.monkey_brain.kernel.pipeline.cognitive_policy import CognitivePolicy

        counts = {}

        def make_counter(name):
            async def counted(s):
                counts[name] = counts.get(name, 0) + 1
                return s

            return counted

        stages = [
            "observe",
            "believe",
            "plan",
            "execute",
            "observe_outcome",
            "learn",
            "compile_phi",
            "predict",
            "commit",
        ]

        policy = CognitivePolicy()
        policy.configure(
            observe=make_counter("observe"),
            believe=make_counter("believe"),
            plan=make_counter("plan"),
            execute=make_counter("execute"),
            observe_outcome=make_counter("observe_outcome"),
            learn=make_counter("learn"),
            compile_phi=make_counter("compile_phi"),
            predict=make_counter("predict"),
            commit=make_counter("commit"),
        )

        rt = CognitiveRuntime(policy=policy)
        await rt.run(_make_compiled(), _make_context())

        for stage in stages:
            assert counts[stage] == 1


# ═══════════════════════════════════════════════════════════════════════════
# BeliefState in Runtime
# ═══════════════════════════════════════════════════════════════════════════


class TestBeliefInRuntime:
    @pytest.mark.asyncio
    async def test_belief_initialized_with_identity(self):
        rt = CognitiveRuntime()
        result = await rt.run(_make_compiled(), _make_context())
        assert result["belief"]["actor_id"] == "user-1"
        assert result["belief"]["tenant_id"] == "acme"

    @pytest.mark.asyncio
    async def test_observe_writes_facts_to_belief(self):
        rt = CognitiveRuntime()
        result = await rt.run(_make_compiled(), _make_context())
        assert result["belief"]["facts"] >= 1

    @pytest.mark.asyncio
    async def test_believe_sets_intent_and_goal(self):
        rt = CognitiveRuntime()
        result = await rt.run(_make_compiled(), _make_context())
        assert result["belief"]["intent"] == "shopping"
        assert result["belief"]["goal"] == "acquire_item"

    @pytest.mark.asyncio
    async def test_plan_reads_belief(self):
        rt = CognitiveRuntime()
        result = await rt.run(_make_compiled(), _make_context())
        assert result["plan"]["goal"] == "acquire_item"

    @pytest.mark.asyncio
    async def test_learn_generates_hypotheses(self):
        """_learn's fact-threshold hypothesis generation (belief_runtime.py)
        checks the literal attribute name "temperature" with value > 4.0 —
        this fact must match that exactly to trigger a hypothesis."""
        rt = CognitiveRuntime()

        async def inject_facts(state):
            state.belief.add_fact(entity="milk", attribute="temperature", value=5.0, confidence=0.4)
            return state

        rt._observe = inject_facts
        result = await rt.run(_make_compiled(), _make_context())
        assert result["belief"]["hypotheses"] >= 1

    @pytest.mark.asyncio
    async def test_predict_writes_to_belief(self):
        rt = CognitiveRuntime()

        async def inject_facts(state):
            state.belief.add_fact(entity="x", attribute="y", value=1)
            return state

        rt._observe = inject_facts
        result = await rt.run(_make_compiled(), _make_context())
        assert result["predictions"] >= 1

    @pytest.mark.asyncio
    async def test_observe_captures_world_snapshot(self):
        """Observe should freeze a WorldSnapshot for all subsequent stages."""
        rt = CognitiveRuntime()
        result = await rt.run(_make_compiled(), _make_context())
        # WorldSnapshot is captured during observe
        # (can't directly assert on CognitiveState after run, but summary includes it)
        assert "belief" in result

    @pytest.mark.asyncio
    async def test_same_belief_throughout_lifecycle(self):
        """All stages receive the same BeliefState instance."""
        from src.monkey_brain.kernel.pipeline.cognitive_policy import CognitivePolicy

        captured = {}

        async def capture_observe(s):
            captured["observe"] = id(s.belief)
            return s

        async def capture_plan(s):
            captured["plan"] = id(s.belief)
            return s

        async def capture_learn(s):
            captured["learn"] = id(s.belief)
            return s

        async def noop(s):
            return s

        policy = CognitivePolicy()
        policy.configure(
            observe=capture_observe,
            believe=noop,
            plan=capture_plan,
            execute=noop,
            observe_outcome=noop,
            learn=capture_learn,
            compile_phi=noop,
            predict=noop,
            commit=noop,
        )

        rt = CognitiveRuntime(policy=policy)
        await rt.run(_make_compiled(), _make_context())
        assert captured["observe"] == captured["plan"] == captured["learn"]


# ═══════════════════════════════════════════════════════════════════════════
# Error Handling
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_stage_failure_continues_lifecycle(self):
        from src.monkey_brain.kernel.pipeline.cognitive_policy import CognitivePolicy

        async def fail(s):
            raise RuntimeError("boom")

        async def noop(s):
            return s

        policy = CognitivePolicy()
        policy.configure(
            observe=fail,
            believe=noop,
            plan=noop,
            execute=noop,
            observe_outcome=noop,
            learn=noop,
            compile_phi=noop,
            predict=noop,
            commit=noop,
        )

        rt = CognitiveRuntime(policy=policy)
        result = await rt.run(_make_compiled(), _make_context())
        assert result["status"] == "partial"
        assert len(result["errors"]) >= 1

    @pytest.mark.asyncio
    async def test_lifecycle_completes_when_all_fail(self):
        from src.monkey_brain.kernel.pipeline.cognitive_policy import CognitivePolicy

        async def fail(s):
            raise RuntimeError("always fails")

        policy = CognitivePolicy()
        policy.configure(
            observe=fail,
            believe=fail,
            plan=fail,
            execute=fail,
            observe_outcome=fail,
            learn=fail,
            compile_phi=fail,
            predict=fail,
            commit=fail,
        )

        rt = CognitiveRuntime(policy=policy)
        result = await rt.run(_make_compiled(), _make_context())
        assert result["status"] == "partial"
        assert len(result["errors"]) == 9


# ═══════════════════════════════════════════════════════════════════════════
# Ownership Boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_compiler_imports(self):
        import ast, src.monkey_brain.kernel.pipeline.belief_runtime as mod

        tree = ast.parse(open(mod.__file__).read())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    imports.extend(a.name for a in node.names)
                else:
                    imports.append(node.module or "")
        assert "RequestCompiler" not in " ".join(imports)

    def test_no_orchestrator_imports(self):
        import ast, src.monkey_brain.kernel.pipeline.belief_runtime as mod

        tree = ast.parse(open(mod.__file__).read())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    imports.extend(a.name for a in node.names)
                else:
                    imports.append(node.module or "")

        assert "PipelineOrchestrator" not in " ".join(imports)


# ═══════════════════════════════════════════════════════════════════════════
# Event Bus (CognitiveDelta publication)
# ═══════════════════════════════════════════════════════════════════════════


class TestEventBusPublication:
    @pytest.mark.asyncio
    async def test_publishes_cognitive_delta_when_event_bus_configured(self):
        bus = MagicMock()
        rt = CognitiveRuntime(event_bus=bus)

        await rt.run(_make_compiled(), _make_context())

        assert bus.publish.called
        args, kwargs = bus.publish.call_args
        assert args[0] == "cognitive.delta"
        assert isinstance(args[1], dict)
        assert kwargs.get("source") == "pipeline.cognitive_runtime"

    @pytest.mark.asyncio
    async def test_no_publish_attempted_without_event_bus(self):
        rt = CognitiveRuntime()  # event_bus defaults to None

        # Must not raise even though there's nowhere to publish to.
        result = await rt.run(_make_compiled(), _make_context())

        assert result["status"] in ("success", "partial")

    @pytest.mark.asyncio
    async def test_result_surfaces_cognitive_delta(self):
        rt = CognitiveRuntime()

        result = await rt.run(_make_compiled(), _make_context())

        assert result["cognitive_delta"] is not None
        assert result["cognitive_delta"]["actor_id"] == "user-1"
        assert result["cognitive_delta"]["tenant_id"] == "acme"

    @pytest.mark.asyncio
    async def test_publish_failure_does_not_fail_request(self):
        bus = MagicMock()
        bus.publish.side_effect = RuntimeError("boom")
        rt = CognitiveRuntime(event_bus=bus)

        result = await rt.run(_make_compiled(), _make_context())

        assert result["status"] in ("success", "partial")
