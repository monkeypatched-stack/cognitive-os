"""Learning Hardening Tests — the 15 focused tests from the task spec.

Scope: kernel/pipeline/comparison/integration.py's `_learn_transitions`/
`_apply_transition_learning` (the "learn_transitions" stage) and
kernel/pipeline/belief_runtime.py's `_learn`/`_predict` stages -- the live
actor-tick Learning path. NOT kernel/pipeline/prediction/transitions.py
(learn_from_execution's own EMA math is exercised, not modified) and NOT
kernel/comparator_runtime.py (the Comparator itself -- these tests treat
its output as a fixture, matching how the real pipeline consumes it).

Central, verified finding this pass: the requirement that success must
come from Comparator-VERIFIED outcome, not raw execution success/HTTP-200,
was already substantially implemented in _learn_transitions before this
pass (it already required a real state.comparison_result, bailed on
inconclusive/no_change outcomes, and required real per-node evidence).
This pass found and fixed three real bugs surfaced by direct functional
testing (not just static reading):
  1. Goal-key derivation mismatch between Learn and Predict (learned
     evidence could be written under a key Prediction would never look
     up under) -- and a second-order bug within that same fix (using
     state.plan instead of state.belief.plan, the actual field
     Prediction reads).
  2. A garbled-text bug in _learn (iterating known_transitions.items()
     bound the whole (goal_key, action_key) tuple to one variable).
  3. _predict's dead lookup (bare string membership check against a
     tuple-keyed dict, always False) -- user-approved exception to "do
     not modify prediction," since it only affects a display/side-channel,
     not real decision-making.
  4. A genuine cold-start SUCCESS was being recorded as NEGATIVE evidence,
     because the pre-existing gating required the outcome to match what
     was PREDICTED (impossible for a first-ever observation, where
     nothing was predicted) -- confirmed via a real two-tick pipeline run
     that this made the very next prediction worse, not better, after a
     genuine success. Fixed: learning now trusts the Comparator's
     per-node `actual_success` directly.
  5. The `confidence` fed into learn_from_execution was derived from
     epistemic_loss (prediction ACCURACY), which is always poor at cold
     start by definition -- capping a genuine success's learned
     probability near 0.35 instead of trending toward the real ceiling.
     Fixed: a flat, high confidence (observation reliability, not
     prediction accuracy).

Per this session's standing convention, this file is written but not
executed by the assistant. Run with:
    python -m pytest tests/unit/test_learning_hardening.py -v
"""

from __future__ import annotations

import uuid

import pytest

import src.monkey_brain.kernel.comparator_runtime as comparator_module
from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime
from src.monkey_brain.kernel.pipeline.comparison.integration import (
    _run_comparison,
    _apply_transition_learning,
    ComparisonIntegratedPolicy,
)
from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime
from src.monkey_brain.kernel.pipeline.belief_state import BeliefState, Plan, PlanStep
from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.execution import ExecutionResult, ActionOutcome
from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionModel,
    TransitionPredictionEngine,
)


class _FakePolicy:
    """Mirrors ComparisonIntegratedPolicy's own `_transition_model`
    accumulation contract -- `_apply_transition_learning` only needs an
    object with this one attribute."""

    def __init__(self, transition_model: TransitionModel | None = None) -> None:
        self._transition_model = transition_model or TransitionModel()


def _prediction_result(step_names: tuple[str, ...], predicted_success: bool = True) -> dict:
    outcomes = [{"description": name, "success": predicted_success, "probability": 0.9} for name in step_names]
    candidate = {
        "prediction": {
            "world_snapshot": {},
            "predicted_outcomes": outcomes,
            "expected_utility": 0.8,
        },
        "scenario_label": "Baseline",
        "probability": 0.9,
    }
    return {"candidates": [candidate], "selected": candidate}


def _state(
    actor_id: str,
    goal: str,
    step_names: tuple[str, ...],
    execution_id: str,
    predicted_success: bool = True,
    depends_on: dict[str, tuple[int, ...]] | None = None,
) -> CognitiveState:
    depends_on = depends_on or {}
    plan = Plan(
        goal=goal,
        cost=0.0,
        confidence=0.9,
        risk=0.0,
        planner="llm",
        steps=tuple(PlanStep(action=n, description=n, depends_on=depends_on.get(n, ())) for n in step_names),
    )
    actor = Actor(actor_id=actor_id, tenant_id="acme")
    belief = BeliefState(actor_id=actor_id, tenant_id="acme")
    # _learn_transitions (comparison/integration.py) computes goal_key
    # from belief.goal.name/description, not plan.goal -- goal_key
    # contamination fix, earlier this session. belief.goal is a derived
    # property backed by GoalTimeline, so it must be set via
    # update_goal(), not just constructing Plan(goal=goal, ...); without
    # this every test using this helper silently learned under goal_key
    # "" regardless of the `goal` argument, making cross-goal isolation
    # untestable (and TestTransitionIdentityIsolation/TestActorIsolation
    # below vacuously pass no matter what).
    belief.update_goal(name=goal)
    belief.plan = plan
    state = CognitiveState(actor=actor, belief=belief)
    state.plan = plan  # real ticks always keep state.plan/state.belief.plan in sync
    state.metrics = {"execution_id": execution_id}
    state.prediction_result = _prediction_result(step_names, predicted_success=predicted_success)
    return state


def _with_execution(state: CognitiveState, outcomes: tuple[bool, ...]) -> CognitiveState:
    actions = tuple(
        ActionOutcome(
            action_id=f"a{i}",
            success=ok,
            result={},
            error="" if ok else "failed",
            latency_ms=1.0,
        )
        for i, ok in enumerate(outcomes)
    )
    success_count = sum(1 for o in outcomes if o)
    state.execution_result = ExecutionResult(
        actions=actions,
        success_count=success_count,
        failure_count=len(outcomes) - success_count,
        goal_achieved=all(outcomes),
    )
    return state


@pytest.fixture(autouse=True)
def _fresh_comparator(monkeypatch):
    """Every test gets its own ComparatorRuntime instance -- avoids
    cross-test state via the process-wide singleton the real code path
    resolves through get_comparator_runtime()."""
    monkeypatch.setattr(comparator_module, "get_comparator_runtime", lambda: ComparatorRuntime())


_learn_tick_counter = 0


async def _learn_tick(
    actor_id: str,
    goal: str,
    step_names: tuple[str, ...],
    outcomes: tuple[bool, ...],
    policy: _FakePolicy,
    predicted_success: bool = True,
    depends_on: dict[str, tuple[int, ...]] | None = None,
) -> CognitiveState:
    # execution_id must be unique PER CALL, not per actor: _learn_transitions
    # (comparison/integration.py) gates on already_learned = learning
    # events already recorded for THIS execution_id, specifically to make
    # a genuine crash-resume replay idempotent (never double-learn the
    # same real tick twice). A test that intentionally makes several
    # SEPARATE simulated ticks for the same actor (EMA blending, repeated-
    # learning, policy-persistence-across-ticks) needs each to look like a
    # distinct real tick too, or the second call's evidence is silently
    # (and correctly, per that guard's own real purpose) discarded as an
    # apparent duplicate of the first.
    global _learn_tick_counter
    _learn_tick_counter += 1
    state = _state(
        actor_id,
        goal,
        step_names,
        f"exec-{actor_id}-{_learn_tick_counter}",
        predicted_success=predicted_success,
        depends_on=depends_on,
    )
    state = _with_execution(state, outcomes)
    state = await _run_comparison(state, policy)
    state = _apply_transition_learning(state, policy)
    return state


# ═══════════════════════════════════════════════════════════════════════════
# Test 1 / 11: Successful transition learning + Comparator is the source.
# ═══════════════════════════════════════════════════════════════════════════


class TestSuccessfulTransitionLearning:
    @pytest.mark.asyncio
    async def test_positive_evidence_recorded_for_a_verified_success(self):
        policy = _FakePolicy()
        before = dict(policy._transition_model.known_transitions)
        state = await _learn_tick("arjun", "buy milk", ("BuyMilk",), (True,), policy)

        assert before == {}  # nothing learned yet
        key = ("buy milk", "BuyMilk")
        assert key in policy._transition_model.known_transitions
        after = policy._transition_model.known_transitions[key][-1]
        assert after.probability > 0.5
        assert state.comparison_result["outcome"] in ("success", "unexpected_success")

    @pytest.mark.asyncio
    async def test_comparator_is_the_authoritative_evidence_source_not_raw_execution(
        self,
    ):
        """Capability succeeds, but the Comparator's own outcome says
        nothing verified happened (inconclusive) -- Learning must not
        learn anything from the raw ActionOutcome.success=True alone."""
        policy = _FakePolicy()
        state = _state("arjun", "buy milk", ("BuyMilk",), "exec-inconclusive")
        state = _with_execution(state, (True,))
        # Force an inconclusive Comparator result directly (simulates
        # "actual state cannot be established" -- e.g. Comparator itself
        # failed) without going through a real compare() call.
        state.comparison_result = {"outcome": "inconclusive", "node_diffs": {}}
        result_state = _apply_transition_learning(state, policy)

        assert not policy._transition_model.known_transitions
        assert result_state.transition_model is policy._transition_model


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: Failed transition learning.
# ═══════════════════════════════════════════════════════════════════════════


class TestFailedTransitionLearning:
    @pytest.mark.asyncio
    async def test_negative_evidence_recorded_for_a_verified_failure(self):
        policy = _FakePolicy()
        state = await _learn_tick("arjun", "buy milk", ("BuyMilk",), (False,), policy, predicted_success=True)

        key = ("buy milk", "BuyMilk")
        assert key in policy._transition_model.known_transitions
        after = policy._transition_model.known_transitions[key][-1]
        assert after.probability < 0.5


# ═══════════════════════════════════════════════════════════════════════════
# Test 3 / 4: Partial execution learning; unexecuted node is not a failure.
# ═══════════════════════════════════════════════════════════════════════════


class TestPartialExecutionLearning:
    @pytest.mark.asyncio
    async def test_b_fails_c_not_executed_b_is_negative_c_is_untouched(self):
        policy = _FakePolicy()
        # A and B genuinely execute (A succeeds, B fails); C is never
        # attempted at all -- only 2 ActionOutcomes exist, matching what a
        # dependency-blocked/never-reached step produces at the Comparator
        # boundary (no node_diffs entry, not a False entry).
        state = _state(
            "arjun",
            "buy groceries",
            ("A", "B", "C"),
            "exec-partial",
            depends_on={"C": (1,)},
        )
        state = _with_execution(state, (True, False))  # only A, B ran
        state = await _run_comparison(state, policy)
        state = _apply_transition_learning(state, policy)

        assert state.comparison_result["outcome"] == "partial_success"
        assert state.comparison_result["node_diffs"]["C"]["actual_success"] is None

        key_a, key_b, key_c = (
            ("buy groceries", "A"),
            ("buy groceries", "B"),
            ("buy groceries", "C"),
        )
        assert policy._transition_model.known_transitions[key_a][-1].probability > 0.5
        assert policy._transition_model.known_transitions[key_b][-1].probability < 0.5
        assert key_c not in policy._transition_model.known_transitions, (
            "C never executed -- must not be learned as a failure (or at all)"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Test 5 / 9: No-history / cold-start behavior + recovery.
# ═══════════════════════════════════════════════════════════════════════════


class TestColdStartAndRecovery:
    def test_unknown_action_defaults_to_honest_half_probability_zero_confidence(self):
        """Not modified this pass (prediction-owned) -- verified as the
        real, correct cold-start contract Learning builds on top of."""
        engine = TransitionPredictionEngine(TransitionModel())
        t = engine._unknown_transition(type("A", (), {"description": "x"})())
        assert t.probability == 0.5
        assert t.confidence == 0.0

    @pytest.mark.asyncio
    async def test_one_bad_first_observation_does_not_crash_to_zero(self):
        policy = _FakePolicy()
        state = await _learn_tick("arjun", "buy milk", ("BuyMilk",), (False,), policy)
        prob = policy._transition_model.known_transitions[("buy milk", "BuyMilk")][-1].probability
        assert prob >= 0.05, "a single bad observation must not crash probability to (near) zero"

    async def _run_decide_gate(self, epsilon: float) -> tuple[bool, str | None]:
        """Drives CognitiveRuntime._execute_plan directly against real
        negative prediction evidence (selected is None, not all-unknown) --
        the exact gate the recovery mechanism lives inside
        (belief_runtime.py::_execute_plan). Returns (executed, override_reason)."""

        class _StubCapability:
            def __init__(self) -> None:
                self.call_count = 0

            def handle(self, args: dict) -> dict:
                self.call_count += 1
                return {"success": True, "error": ""}

        class _SpyBus:
            def __init__(self, capabilities: dict) -> None:
                self._capabilities = capabilities

            def discover(self, name: str):
                return self._capabilities.get(name)

            def names(self):
                return list(self._capabilities.keys())

        plan = Plan(
            goal="buy groceries",
            steps=(PlanStep(action="ProductSelection", description="find milk"),),
            cost=0.0,
            confidence=0.8,
            risk=0.0,
            planner="llm",
        )
        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        belief.plan = plan
        belief.metadata["_resolved_permissions"] = frozenset({"ProductSelection"})
        state = CognitiveState(actor=actor, belief=belief)
        # execution_id must be unique per call: a hardcoded, reused id
        # here made every call after the first one that actually
        # executed look like a resume of that SAME prior execution to
        # execution_checkpoint_store.py's real crash-resume mechanism --
        # it silently replayed the cached "already completed" result
        # instead of re-invoking the capability, which made the epsilon
        # roll itself look broken (0/300 observed) when it was actually
        # working correctly the whole time. Confirmed live: the exact
        # same test with a unique id per call landed at 14/300 (4.7%),
        # matching the configured 5% almost exactly.
        state.metrics = {"execution_id": f"exec-{uuid.uuid4().hex}"}
        state.prediction_result = {
            "candidates": [
                {"prediction": {"predicted_outcomes": [{"metadata": {"kind": "known"}, "success_probability": 0.1}]}}
            ],
            "selected": None,
            "rationale": "predicted failure",
        }
        capability = _StubCapability()
        bus = _SpyBus({"ProductSelection": capability})
        executor = ActionExecutor(capability_bus=bus)
        rt = CognitiveRuntime(execution_engine=executor, exploration_epsilon=epsilon)
        result_state = await rt._execute_plan(state)
        return capability.call_count > 0, result_state.metrics.get("decision_override_reason")

    @pytest.mark.asyncio
    async def test_recovery_mechanism_never_fires_at_zero_epsilon(self):
        """LEARNING RECOVERY GAP (task's own expected term) -- CLOSED this
        follow-up pass: kernel/pipeline/belief_runtime.py's decide-stage
        rejection gate (_execute_plan) now has a fixed-probability
        exploration override (exploration_epsilon, default 0.05) alongside
        the pre-existing all-unknown cold-start carve-out. It does NOT
        reset or overwrite any learned probability -- it only occasionally
        lets a real negative-evidence rejection execute anyway, so the
        unmodified learn_from_execution() EMA naturally incorporates
        fresh evidence. With epsilon=0.0 the gate must behave exactly as
        before this pass: real negative evidence is always rejected."""
        executed, override = await self._run_decide_gate(epsilon=0.0)
        assert executed is False
        assert override is None

    @pytest.mark.asyncio
    async def test_recovery_mechanism_always_fires_at_full_epsilon(self):
        executed, override = await self._run_decide_gate(epsilon=1.0)
        assert executed is True
        assert override is not None and "epsilon-exploration" in override

    @pytest.mark.asyncio
    async def test_recovery_mechanism_default_epsilon_rate_is_low_but_nonzero(self):
        """Statistical check on the default (0.05) rate -- confirms
        `random.random()` (the one place randomness enters, per the
        original task's own allowance for stochastic exploration) is
        wired correctly, without pinning an exact count."""
        explored = 0
        trials = 300
        for _ in range(trials):
            executed, _ = await self._run_decide_gate(epsilon=0.05)
            if executed:
                explored += 1
        rate = explored / trials
        assert 0.01 < rate < 0.15, f"observed explore rate {rate} too far from expected ~0.05"


# ═══════════════════════════════════════════════════════════════════════════
# Test 6: Repeated evidence update -- verify the MATH, not just "changed".
# ═══════════════════════════════════════════════════════════════════════════


class TestRepeatedEvidenceUpdate:
    @pytest.mark.asyncio
    async def test_sequence_matches_the_documented_ema_formula_exactly(self):
        policy = _FakePolicy()
        learning_rate = 0.15  # hardcoded in _learn_transitions
        confidence = 0.85  # hardcoded in _learn_transitions (this pass's fix)

        sequence = (True, True, False, True)
        expected: list[float] = []
        p: float | None = None
        for ok in sequence:
            observed = min(0.95, confidence) if ok else max(0.05, 1.0 - confidence)
            p = round(observed, 4) if p is None else round(p * (1 - learning_rate) + observed * learning_rate, 4)
            expected.append(p)

        actual = []
        for ok in sequence:
            state = await _learn_tick("arjun", "g", ("A",), (ok,), policy)
            actual.append(policy._transition_model.known_transitions[("g", "A")][-1].probability)

        assert actual == expected


# ═══════════════════════════════════════════════════════════════════════════
# Test 7: Transition identity isolation -- unrelated transitions don't
# contaminate each other (the exact regression class the task names).
# ═══════════════════════════════════════════════════════════════════════════


class TestTransitionIdentityIsolation:
    @pytest.mark.asyncio
    async def test_same_action_name_different_goal_does_not_collide(self):
        policy = _FakePolicy()
        await _learn_tick("arjun", "buy milk", ("Checkout",), (False,), policy)
        await _learn_tick("arjun", "buy furniture", ("Checkout",), (True,), policy)

        milk_key = ("buy milk", "Checkout")
        furniture_key = ("buy furniture", "Checkout")
        assert policy._transition_model.known_transitions[milk_key][-1].probability < 0.5
        assert policy._transition_model.known_transitions[furniture_key][-1].probability > 0.5

    @pytest.mark.asyncio
    async def test_failure_of_one_transition_does_not_poison_an_unrelated_one(self):
        """Critical regression test named explicitly in the task: Transition
        A failing must not make Transition B unlikely unless they
        genuinely share the same (goal_key, action_key) identity."""
        policy = _FakePolicy()
        await _learn_tick("arjun", "buy milk", ("ChargeCard",), (False,), policy)  # A fails badly
        prediction_engine = TransitionPredictionEngine(policy._transition_model)

        class _Action:
            description = "ScheduleDelivery"

        # B ("ScheduleDelivery" under the SAME goal) was never touched.
        b_transitions = prediction_engine.predict_transitions(None, None, _Action(), goal_key="buy milk")
        assert b_transitions[0].kind.value == "unknown"
        assert b_transitions[0].probability == 0.5, (
            "B must remain at the honest cold-start default, not be dragged down by A"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Test 8: Actor isolation.
# ═══════════════════════════════════════════════════════════════════════════


class TestActorIsolation:
    @pytest.mark.asyncio
    async def test_evidence_from_one_actor_does_not_touch_another(self):
        policy_alice = _FakePolicy()
        policy_bob = _FakePolicy()
        await _learn_tick("alice", "g", ("A",), (True,), policy_alice)
        await _learn_tick("bob", "g", ("A",), (False,), policy_bob)

        p_alice = policy_alice._transition_model.known_transitions[("g", "A")][-1].probability
        p_bob = policy_bob._transition_model.known_transitions[("g", "A")][-1].probability
        assert p_alice > 0.5
        assert p_bob < 0.5
        assert policy_alice._transition_model is not policy_bob._transition_model


# ═══════════════════════════════════════════════════════════════════════════
# Test 10: Persistence (TransitionModel -- the durable learned state).
# ═══════════════════════════════════════════════════════════════════════════


class TestPersistence:
    def test_transition_model_round_trips_through_the_declared_serialization_contract(
        self,
    ):
        model = TransitionModel().learn_from_execution(
            action_key="BuyMilk",
            success=True,
            confidence=0.85,
            world_delta={},
            learning_rate=0.15,
            goal_key="buy milk",
        )
        restored = TransitionModel.from_dict(model.to_dict())
        assert restored.known_transitions.keys() == model.known_transitions.keys()
        for key in model.known_transitions:
            assert restored.known_transitions[key][-1].probability == model.known_transitions[key][-1].probability

    def test_old_pre_goal_scoping_flat_dict_is_discarded_not_migrated(self):
        """Not modified this pass -- verified as the correct, still-live
        behavior from the earlier Cross-Goal Plan Contamination fix:
        carrying old, un-goal-scoped data forward under a fabricated
        goal_key would be dishonest, so it's dropped instead."""
        restored = TransitionModel.from_dict({"known_transitions": {"BuyMilk": []}})
        assert restored.known_transitions == {}


# ═══════════════════════════════════════════════════════════════════════════
# Test 12: Deterministic update.
# ═══════════════════════════════════════════════════════════════════════════


class TestDeterministicUpdate:
    @pytest.mark.asyncio
    async def test_identical_inputs_produce_identical_learned_model(self):
        policy_a = _FakePolicy()
        policy_b = _FakePolicy()
        await _learn_tick("dana", "g", ("A",), (True,), policy_a)
        await _learn_tick("dana", "g", ("A",), (True,), policy_b)
        assert policy_a._transition_model.known_transitions == policy_b._transition_model.known_transitions


# ═══════════════════════════════════════════════════════════════════════════
# Test 13 / 14: Learning does not mutate the plan or execution history.
# ═══════════════════════════════════════════════════════════════════════════


class TestNoUnrelatedMutation:
    @pytest.mark.asyncio
    async def test_plan_and_execution_result_are_untouched_by_learning(self):
        policy = _FakePolicy()
        state = _state("arjun", "g", ("A",), "exec-nomut")
        state = _with_execution(state, (True,))
        plan_before = state.plan
        exec_before = state.execution_result
        action_before = state.execution_result.actions[0]

        state = await _run_comparison(state, policy)
        state = _apply_transition_learning(state, policy)

        assert state.plan is plan_before
        assert state.belief.plan is plan_before
        assert state.execution_result is exec_before
        assert state.execution_result.actions[0] is action_before
        assert state.execution_result.actions[0].success is True


# ═══════════════════════════════════════════════════════════════════════════
# Test 15: Learned state can subsequently affect prediction correctly.
# ═══════════════════════════════════════════════════════════════════════════


class TestPredictionIntegration:
    @pytest.mark.asyncio
    async def test_a_learned_transition_is_reflected_in_the_next_ticks_prediction(self):
        """Exercises the real, fixed _predict stage directly (Fix 3) --
        not just the underlying TransitionPredictionEngine, which was
        already correct before this pass."""
        policy = ComparisonIntegratedPolicy(transition_model=TransitionModel())
        CognitiveRuntime(policy=policy)  # binds plan/execute for configure()
        stage_map = dict(policy._stages)
        predict_stage = stage_map["predict"]
        compare_stage = stage_map["compare"]
        learn_transitions_stage = stage_map["learn_transitions"]

        def make(execution_id):
            plan = Plan(
                goal="buy milk",
                steps=(PlanStep(action="BuyMilk", description="buy milk"),),
                cost=0.0,
                confidence=0.9,
                risk=0.0,
                planner="llm",
            )
            actor = Actor(actor_id="arjun", tenant_id="acme")
            belief = BeliefState(actor_id="arjun", tenant_id="acme")
            belief.plan = plan
            state = CognitiveState(actor=actor, belief=belief)
            state.plan = plan
            state.metrics = {"execution_id": execution_id}
            return state

        s1 = await predict_stage(make("t1"))
        assert "unknown" in s1.belief.predictions[-1].description

        s1.execution_result = ExecutionResult(
            actions=(ActionOutcome(action_id="a0", success=True, result={}, latency_ms=1.0),),
            success_count=1,
            failure_count=0,
            goal_achieved=True,
        )
        s1 = await compare_stage(s1)
        s1 = await learn_transitions_stage(s1)
        assert policy._transition_model.known_transitions

        s2 = await predict_stage(make("t2"))
        assert "unknown" not in s2.belief.predictions[-1].description
        assert "%" in s2.belief.predictions[-1].description


# ═══════════════════════════════════════════════════════════════════════════
# Gap D ("fix the gaps" follow-up): minimal PolicyStore integration.
# ═══════════════════════════════════════════════════════════════════════════


class TestPolicyStoreIntegration:
    """A full GraphManager/BellmanPolicy merge was investigated and found
    architecturally incoherent (incompatible key shapes -- see
    comparison/integration.py::_apply_transition_learning's docstring).
    Instead, kernel/policy/store.py::PolicyStore (confirmed standalone) is
    wired into the same Comparator-gated evidence _learn_transitions
    already uses for TransitionModel, keyed identically:
    (goal_key, action_key)."""

    @pytest.mark.asyncio
    async def test_verified_success_raises_the_policy_stores_q_value(self):
        policy = _FakePolicy()
        assert not hasattr(policy, "_policy_store")
        await _learn_tick("arjun", "buy milk", ("BuyMilk",), (True,), policy)
        assert hasattr(policy, "_policy_store")
        assert policy._policy_store.value("buy milk", "BuyMilk") > 0.5

    @pytest.mark.asyncio
    async def test_verified_failure_lowers_the_policy_stores_q_value(self):
        policy = _FakePolicy()
        await _learn_tick("arjun", "buy milk", ("BuyMilk",), (False,), policy)
        assert policy._policy_store.value("buy milk", "BuyMilk") < 0.5

    @pytest.mark.asyncio
    async def test_policy_store_persists_across_ticks_not_recreated(self):
        policy = _FakePolicy()
        await _learn_tick("arjun", "buy milk", ("BuyMilk",), (True,), policy)
        store_ref = policy._policy_store
        await _learn_tick("arjun", "buy milk", ("BuyMilk",), (True,), policy)
        assert policy._policy_store is store_ref
        assert policy._policy_store.update_count == 2

    @pytest.mark.asyncio
    async def test_unexecuted_node_does_not_touch_the_policy_store(self):
        """Same gate as TransitionModel: C is unreachable (depends on
        failed B), so it must get neither a TransitionModel entry nor a
        PolicyStore Q-value."""
        policy = _FakePolicy()
        # Only A, B genuinely execute (matches TestPartialExecutionLearning's
        # fixture pattern) -- C is never attempted, no ActionOutcome exists.
        state = await _learn_tick(
            "arjun",
            "buy groceries",
            ("A", "B", "C"),
            (True, False),
            policy,
            depends_on={"C": (1,)},
        )
        assert state.comparison_result["node_diffs"]["C"]["actual_success"] is None
        assert policy._policy_store.value("buy groceries", "C") == 0.5  # untouched neutral prior
        assert ("buy groceries", "C") not in policy._policy_store.values()
