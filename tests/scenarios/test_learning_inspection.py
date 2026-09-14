"""LEARN-001..003 — learning-inspection qualification tests.

kernel/pipeline/learning_event_store.py closes a real gap: TransitionModel
persistence (kernel/pipeline/prediction/persistence.py) only ever keeps the
CURRENT blended snapshot per actor -- each learn_from_execution call
replaces the prior WorldTransition for a (goal_key, action_key), so "what
changed as a result of this specific execution" was unrecoverable the
moment the next tick ran. This is read-only/additive: no change to
execution, planning, or learning behavior, only to what's observable about
learning that already happens (kernel/pipeline/comparison/integration.py::
_learn_transitions, unchanged in its own logic, now also records a before/
after event once it decides a real, Comparator-verified update happened).

LEARN-001 is the spec's "Cross-Run Learning Test": Execution 1 runs the
real Comparator -> Learn -> Redis-persistence chain; "restart" is
simulated the same honest way Phase 3's RECOVERY tests do it (a fresh
load_transition_model() call, not a reused in-memory object), and the
resulting TransitionPredictionEngine's prediction is asserted to actually
reflect what was learned -- proving Execution -> Comparator -> Learning ->
Persistence -> (new tick) -> Prediction is a real, connected chain, not
just individually-working pieces.
"""

from __future__ import annotations

import pytest

ACTOR_ID_001 = "learn_test_actor_001"
ACTOR_ID_002 = "learn_test_actor_002"
ACTOR_ID_003 = "learn_test_actor_003"


@pytest.mark.asyncio
async def test_learn001_cross_run_learning_prediction_consumes_persisted_state(
    monkeypatch,
):
    """Execution 1: a real ProductSelection success runs through the full
    Comparator -> Learn -> Redis-persistence chain. "Restart": a fresh
    load_transition_model() call (a real Redis round-trip, not the same
    in-memory policy) feeds a brand new TransitionPredictionEngine, whose
    prediction for the SAME (goal_key, action_key) must reflect what was
    actually learned -- not the honest-but-uninformative UNKNOWN/0.5
    default a cold actor gets for the same action.
    """
    import src.monkey_brain.kernel.comparator_runtime as comparator_module
    from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime
    from src.monkey_brain.kernel.pipeline.actor import Actor
    from src.monkey_brain.kernel.pipeline.belief_state import (
        BeliefState,
        Plan,
        PlanStep,
    )
    from src.monkey_brain.kernel.pipeline.comparison.integration import (
        _apply_transition_learning,
        _run_comparison,
    )
    from src.monkey_brain.kernel.pipeline.execution import (
        ActionOutcome,
        ExecutionResult,
    )
    from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
    from src.monkey_brain.kernel.pipeline.learning_event_store import (
        load_learning_events_for_execution,
    )
    from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal
    from src.monkey_brain.kernel.pipeline.prediction.persistence import (
        load_transition_model,
        save_transition_model,
    )
    from src.monkey_brain.kernel.pipeline.prediction.transitions import (
        TransitionKind,
        TransitionModel,
        TransitionPredictionEngine,
    )

    goal = "buy milk (cross-run learning test)"
    execution_id = "learn-001-execution-1"
    goal_key = canonicalize_goal(goal)

    # Reset any residue from a prior run of this same test -- real API,
    # not a private reach-around (persistence never exposes a delete, and
    # an empty model is a legitimate value to persist).
    save_transition_model(ACTOR_ID_001, TransitionModel())

    plan = Plan(
        goal=goal,
        steps=(PlanStep(action="Milk", description="buy milk", confidence=0.9),),
        cost=0.0,
        confidence=0.9,
        risk=0.0,
        planner="llm",
    )
    actor = Actor(actor_id=ACTOR_ID_001, tenant_id="acme")
    belief = BeliefState(actor_id=ACTOR_ID_001, tenant_id="acme")
    # _learn_transitions computes goal_key from belief.goal, not plan.goal
    # -- see test_compound_disruption.py's identical fix for the full
    # account.
    belief.update_goal(name=goal)
    belief.plan = plan
    state = CognitiveState(actor=actor, belief=belief)
    state.metrics = {"execution_id": execution_id}

    def _predicted(desc):
        return {
            "prediction": {
                "world_snapshot": {},
                "predicted_outcomes": [{"description": desc, "success": True, "probability": 0.5}],
                "expected_utility": 0.5,
            },
            "scenario_label": "Baseline",
            "probability": 0.5,
        }

    state.prediction_result = {
        "candidates": [_predicted("buy milk")],
        "selected": _predicted("buy milk"),
    }
    state.execution_result = ExecutionResult(
        actions=(
            ActionOutcome(
                action_id=f"{ACTOR_ID_001}_step_0",
                success=True,
                result={"selected": True},
                latency_ms=1.0,
            ),
        ),
        success_count=1,
        failure_count=0,
        goal_achieved=True,
    )

    class FakePolicy:
        def __init__(self):
            self._transition_model = TransitionModel()

    policy = FakePolicy()
    monkeypatch.setattr(comparator_module, "get_comparator_runtime", lambda: ComparatorRuntime())
    result_state = await _run_comparison(state, policy)
    result_state = _apply_transition_learning(result_state, policy)

    assert (goal_key, "Milk") in policy._transition_model.known_transitions

    # A new event was recorded for this execution, with no prior value
    # (cold start).
    events = load_learning_events_for_execution(execution_id)
    assert len(events) == 1
    assert events[0].action_key == "Milk"
    assert events[0].success is True
    assert events[0].previous is None
    assert events[0].updated["probability"] > 0.7

    # "Restart": load the persisted model fresh, as a genuinely new tick
    # would (kernel/pipeline/prediction/persistence.py::load_transition_model,
    # not the in-memory `policy` object above).
    reloaded = load_transition_model(ACTOR_ID_001)
    assert reloaded is not None

    warm_engine = TransitionPredictionEngine(model=reloaded)
    warm_prediction = warm_engine.predict_transitions(
        world_snapshot={},
        belief_state=belief,
        action=plan.steps[0],
        goal_key=goal_key,
    )[0]

    cold_engine = TransitionPredictionEngine(model=TransitionModel())
    cold_prediction = cold_engine.predict_transitions(
        world_snapshot={},
        belief_state=belief,
        action=plan.steps[0],
        goal_key=goal_key,
    )[0]

    # The cold-start actor gets the honest "no knowledge" default...
    assert cold_prediction.kind == TransitionKind.UNKNOWN
    assert cold_prediction.probability == 0.5
    # ...while the actor whose prior execution's learning was actually
    # persisted and reloaded gets a real, different prediction -- proving
    # Prediction consumes learned state across the reload, not just within
    # one in-memory policy object.
    assert warm_prediction.kind != TransitionKind.UNKNOWN
    assert warm_prediction.probability != cold_prediction.probability
    assert warm_prediction.probability > 0.7


def test_learn002_event_store_round_trip():
    """previous/updated capture and actor-scoped history ordering, direct
    against the store (no pipeline involved) -- the deterministic unit-level
    guarantee LEARN-001 builds on."""
    from src.monkey_brain.kernel.pipeline.learning_event_store import (
        LearningEvent,
        load_learning_events_for_actor,
        load_learning_events_for_execution,
        record_learning_event,
    )

    exec_id_1 = "learn-002-execution-1"
    exec_id_2 = "learn-002-execution-2"

    first = LearningEvent(
        execution_id=exec_id_1,
        actor_id=ACTOR_ID_002,
        goal_key="buy milk",
        action_key="Milk",
        success=True,
        previous=None,
        updated={"probability": 0.85, "kind": "probabilistic"},
    )
    assert record_learning_event(first) is True

    loaded = load_learning_events_for_execution(exec_id_1)
    assert len(loaded) == 1
    assert loaded[0].previous is None
    assert loaded[0].updated["probability"] == 0.85

    second = LearningEvent(
        execution_id=exec_id_2,
        actor_id=ACTOR_ID_002,
        goal_key="buy milk",
        action_key="Milk",
        success=True,
        previous=first.updated,
        updated={"probability": 0.9, "kind": "probabilistic"},
    )
    assert record_learning_event(second) is True

    # Execution-scoped lists stay scoped to their own execution_id.
    assert len(load_learning_events_for_execution(exec_id_1)) == 1
    assert len(load_learning_events_for_execution(exec_id_2)) == 1

    # Actor-scoped history is newest-first and carries real provenance:
    # the second event's "previous" is exactly the first event's "updated".
    actor_history = load_learning_events_for_actor(ACTOR_ID_002)
    assert actor_history[0].execution_id == exec_id_2
    assert actor_history[1].execution_id == exec_id_1
    assert actor_history[0].previous == first.updated


@pytest.mark.asyncio
async def test_learn003_blocked_step_produces_no_learning_event(monkeypatch):
    """Regression guard: the new instrumentation in _learn_transitions must
    sit AFTER both existing `continue` gates (no Comparator evidence /
    actual_success is None), so a step that was never executed (blocked by
    a failed dependency) produces zero learning events -- the same
    invariant Phase 2's FAULT-001 already proved for the TransitionModel
    itself, now checked for the event log too.
    """
    import src.monkey_brain.kernel.comparator_runtime as comparator_module
    from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime
    from src.monkey_brain.kernel.pipeline.actor import Actor
    from src.monkey_brain.kernel.pipeline.belief_state import (
        BeliefState,
        Plan,
        PlanStep,
    )
    from src.monkey_brain.kernel.pipeline.comparison.integration import (
        _apply_transition_learning,
        _run_comparison,
    )
    from src.monkey_brain.kernel.pipeline.execution import (
        ActionOutcome,
        ExecutionResult,
    )
    from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
    from src.monkey_brain.kernel.pipeline.learning_event_store import (
        load_learning_events_for_execution,
    )
    from src.monkey_brain.kernel.pipeline.prediction.transitions import TransitionModel

    monkeypatch.setattr(comparator_module, "get_comparator_runtime", lambda: ComparatorRuntime())

    goal = "buy groceries in order (learn-003)"
    execution_id = "learn-003-execution-1"
    plan = Plan(
        goal=goal,
        steps=(
            PlanStep(action="Milk", description="buy milk", confidence=0.9),
            PlanStep(action="Eggs", description="buy eggs", confidence=0.9, depends_on=(0,)),
            PlanStep(action="Bread", description="buy bread", confidence=0.9, depends_on=(1,)),
        ),
        cost=0.0,
        confidence=0.9,
        risk=0.0,
        planner="llm",
    )
    actor = Actor(actor_id=ACTOR_ID_003, tenant_id="acme")
    belief = BeliefState(actor_id=ACTOR_ID_003, tenant_id="acme")
    belief.plan = plan
    state = CognitiveState(actor=actor, belief=belief)
    state.metrics = {"execution_id": execution_id}

    def _predicted(desc):
        return {
            "prediction": {
                "world_snapshot": {},
                "predicted_outcomes": [{"description": desc, "success": True, "probability": 0.9}],
                "expected_utility": 0.5,
            },
            "scenario_label": "Baseline",
            "probability": 0.9,
        }

    state.prediction_result = {
        "candidates": [_predicted("buy milk")],
        "selected": _predicted("buy milk"),
    }
    state.execution_result = ExecutionResult(
        actions=(
            ActionOutcome(
                action_id=f"{ACTOR_ID_003}_step_0",
                success=True,
                result={"selected": True},
                latency_ms=1.0,
            ),
            ActionOutcome(
                action_id=f"{ACTOR_ID_003}_step_1",
                success=False,
                error="Simulated provider outage for eggs",
                result={"forced_failure": True, "capability": "ProductSelection"},
                latency_ms=1.0,
            ),
            ActionOutcome(
                action_id=f"{ACTOR_ID_003}_step_2",
                success=False,
                error="blocked: dependency step 1 did not succeed",
                result={"blocked_by_dependency": 1},
                latency_ms=0.0,
            ),
        ),
        success_count=1,
        failure_count=2,
        goal_achieved=False,
    )

    class FakePolicy:
        def __init__(self):
            self._transition_model = TransitionModel()

    policy = FakePolicy()

    result_state = await _run_comparison(state, policy)
    result_state = _apply_transition_learning(result_state, policy)

    events = load_learning_events_for_execution(execution_id)
    action_keys = [e.action_key for e in events]
    assert action_keys.count("Milk") == 1
    assert action_keys.count("Eggs") == 1
    assert action_keys.count("Bread") == 0
