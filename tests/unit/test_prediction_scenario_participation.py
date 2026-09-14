"""Regression coverage for the Prediction subsystem hardening pass.

Scope-defining finding (confirmed with the user before this pass): the
subsystem fresh plans actually reach post-plan-hysteresis-fix
(kernel/pipeline/prediction/) has no solver registry -- it implements
exactly one real, live-wired prediction ALGORITHM (TransitionModel-based
deterministic simulation -> CounterfactualEngine branching ->
ScenarioEvaluator scoring), inlined directly by
kernel/pipeline/prediction/integration.py::PredictionIntegratedPolicy
.configure(). A `PredictionPolicy` Protocol + `PredictionPolicyRegistry`
(policies.py) exist as unused scaffolding for hypothetical future
strategies and are never registered with more than one implementation
anywhere in the repo -- not exercised here, that's already covered by
test_prediction_policies.py and is not what this pass hardens.

Given that, the real "solver ensemble" in this subsystem is which SCENARIO
SOURCES (the Baseline trajectory + every registered CounterfactualAssumption)
actually get independently evaluated through that one algorithm -- this
file's tests map the task's 12-point solver-participation checklist onto
that real structure (see kernel/pipeline/prediction/scenarios.py::
ScenarioParticipation).

Per this session's standing convention, this file is written but not
executed by the assistant. Run with:
    python -m pytest tests/unit/test_prediction_scenario_participation.py -v
"""

from __future__ import annotations

import asyncio

import pytest

from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.belief_state import BeliefState, Plan, PlanStep
from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionModel,
    TransitionPredictionEngine,
    WorldTransition,
    TransitionKind,
)
from src.monkey_brain.kernel.pipeline.prediction.simulation import SimulationEngine
from src.monkey_brain.kernel.pipeline.prediction.risk import RiskEngine
from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import (
    CounterfactualAssumption,
    CounterfactualEngine,
)
from src.monkey_brain.kernel.pipeline.prediction.scenarios import (
    ScenarioEvaluator,
    ScenarioParticipation,
    scenarios_from_counterfactuals,
    build_scenario_participation,
)
from src.monkey_brain.kernel.pipeline.prediction.integration import (
    PredictionIntegratedPolicy,
)
from src.monkey_brain.kernel.pipeline.learning.domain import Provenance


def _plan(steps: tuple[str, ...], goal: str = "") -> Plan:
    """goal="" -> canonicalize_goal("") == "" too, so TransitionModel keys
    can just use ("", action) directly without recomputing the goal-key
    normalization (kernel/pipeline/planning/goal_key.py::canonicalize_goal)
    in every test."""
    return Plan(
        goal=goal,
        steps=tuple(PlanStep(action=s, description=s) for s in steps),
        cost=0.0,
        confidence=0.8,
        risk=0.0,
        planner="llm",
    )


def _model_with_probability(action_names: tuple[str, ...], probability: float) -> TransitionModel:
    return TransitionModel(
        known_transitions={
            ("", name): (
                WorldTransition(
                    description=f"{name} outcome",
                    probability=probability,
                    confidence=0.9,
                ),
            )
            for name in action_names
        }
    )


def _trajectory(model: TransitionModel, plan: Plan):
    return SimulationEngine(TransitionPredictionEngine(model)).simulate_plan(None, None, plan)


class TestAllRequiredScenariosInvoked:
    """Task Test 1: all registered scenario sources are invoked."""

    def test_baseline_plus_every_assumption_invoked(self):
        plan = _plan(("step_a",))
        model = _model_with_probability(("step_a",), 0.9)
        cf_engine = CounterfactualEngine(model)
        baseline = cf_engine.simulate_baseline(None, None, plan)
        assumptions = (
            CounterfactualAssumption(description="Assumption 1"),
            CounterfactualAssumption(description="Assumption 2"),
        )
        branches = cf_engine.generate_branches(None, None, plan, assumptions)
        participation = build_scenario_participation(
            "Baseline",
            tuple(a.description for a in assumptions),
            branches,
        )
        assert participation.scenarios_invoked == (
            "Baseline",
            "Assumption 1",
            "Assumption 2",
        )
        assert participation.aggregation_status == "complete"


class TestEverySuccessfulScenarioContributes:
    """Task Test 2: every successful scenario appears in the final
    PredictionResult.candidates -- nothing is silently dropped."""

    def test_all_successful_scenarios_appear_as_candidates(self):
        plan = _plan(("step_a",))
        model = _model_with_probability(("step_a",), 0.9)
        cf_engine = CounterfactualEngine(model)
        baseline = cf_engine.simulate_baseline(None, None, plan)
        assumption = CounterfactualAssumption(description="Alt")
        branches = cf_engine.generate_branches(None, None, plan, (assumption,))
        scenarios = scenarios_from_counterfactuals("Baseline", baseline, branches)

        result = ScenarioEvaluator().evaluate_and_recommend(scenarios)

        assert {c.scenario_label for c in result.candidates} == {"Baseline", "Alt"}


class TestSolverFailureExplicitlyRepresented:
    """Task Test 3: a scenario-source simulation failure is recorded, not
    silently treated as success, and does not crash evaluation."""

    def test_failing_branch_recorded_and_excluded_not_crashed(self):
        plan = _plan(("step_a",))
        model = _model_with_probability(("step_a",), 0.9)
        cf_engine = CounterfactualEngine(model)
        baseline = cf_engine.simulate_baseline(None, None, plan)

        class _BoomStep:
            @property
            def description(self):
                raise RuntimeError("boom")

        bad_plan = Plan(goal="", steps=(), cost=0.0, confidence=0.8, risk=0.0, planner="llm")
        object.__setattr__(bad_plan, "steps", (_BoomStep(),))  # force a step that raises during simulation
        assumption = CounterfactualAssumption(description="Broken hypothesis")

        # branch() must not raise even though simulate_plan(bad_plan) will.
        branch = cf_engine.branch(None, None, bad_plan, assumption, baseline=baseline)
        assert branch.error != ""

        scenarios = scenarios_from_counterfactuals("Baseline", baseline, (branch,))
        assert [s.label for s in scenarios] == ["Baseline"]  # failed branch excluded, not faked

        participation = build_scenario_participation("Baseline", ("Broken hypothesis",), (branch,))
        assert participation.aggregation_status == "partial"
        assert participation.scenarios_failed == ({"label": "Broken hypothesis", "error": branch.error},)


class TestUnusedRegisteredScenarioDetected:
    """Task Test 4: a scenario that was required but never actually
    invoked is detectable via the participation record."""

    def test_required_but_not_invoked_is_not_complete(self):
        participation = ScenarioParticipation(
            scenarios_required=("Baseline", "Never Ran"),
            scenarios_invoked=("Baseline",),
            scenarios_succeeded=("Baseline",),
            scenarios_failed=(),
            aggregation_status="complete",  # deliberately wrong, to prove the mismatch is checkable
        )
        assert set(participation.scenarios_required) - set(participation.scenarios_invoked) == {"Never Ran"}
        # The real builder never produces this inconsistency:
        real = build_scenario_participation("Baseline", ("Never Ran",), ())
        assert real.aggregation_status != "complete"
        assert "Never Ran" in real.scenarios_required
        assert "Never Ran" not in real.scenarios_invoked


class TestOutputsNotSilentlyDiscarded:
    """Task Test 5: same evidence as Test 2, asserted as its own
    checklist item per the task's numbering."""

    def test_rejected_scenarios_still_appear_in_candidates(self):
        plan = _plan(("step_a",))
        low_model = _model_with_probability(("step_a",), 0.05)  # will be rejected (<0.3)
        cf_engine = CounterfactualEngine(low_model)
        baseline = cf_engine.simulate_baseline(None, None, plan)
        scenarios = scenarios_from_counterfactuals("Baseline", baseline, ())

        result = ScenarioEvaluator().evaluate_and_recommend(scenarios)

        assert len(result.candidates) == 1
        assert result.candidates[0].rejected is True  # rejected, but still present/visible


class TestOutputsNotDoubleCounted:
    """Task Test 6: scenario probabilities are ranked/selected, never
    multiplied against each other; within one scenario the N-step product
    is computed exactly once."""

    def test_scenarios_are_ranked_not_multiplied(self):
        plan = _plan(("step_a",))
        cf_engine_hi = CounterfactualEngine(_model_with_probability(("step_a",), 0.9))
        baseline_hi = cf_engine_hi.simulate_baseline(None, None, plan)
        cf_engine_lo = CounterfactualEngine(_model_with_probability(("step_a",), 0.2))
        baseline_lo = cf_engine_lo.simulate_baseline(None, None, plan)

        evaluator = ScenarioEvaluator()
        cand_hi = evaluator.evaluate(scenarios_from_counterfactuals("A", baseline_hi, ())[0])
        cand_lo = evaluator.evaluate(scenarios_from_counterfactuals("B", baseline_lo, ())[0])

        # If scenarios were (wrongly) multiplied together instead of each
        # scored independently, neither candidate's own probability would
        # equal its own scenario's real value.
        assert cand_hi.probability == pytest.approx(0.9)
        assert cand_lo.probability == pytest.approx(0.2)
        assert cand_hi.probability != pytest.approx(0.9 * 0.2)

    def test_six_step_product_computed_exactly_once(self):
        plan = _plan(tuple(f"step_{i}" for i in range(6)))
        model = _model_with_probability(tuple(f"step_{i}" for i in range(6)), 0.9)
        trajectory = _trajectory(model, plan)
        assessment = RiskEngine().assess(trajectory)
        assert assessment.probability_of_success == pytest.approx(0.9**6, rel=1e-6)


class TestControlledScenarioProbabilities:
    """Task Test 7: known controlled per-step probabilities produce the
    exact expected scenario result (a real, mathematically-verified,
    non-regressed AND-chain -- risk.py's own docstring: "the product of
    each applied transition's own probability... the honest answer to how
    likely is the predicted path to occur")."""

    @pytest.mark.parametrize("p,expected", [(0.9, 0.9**6), (0.5, 0.5**6), (0.15, 0.15**6)])
    def test_six_step_plan_matches_expected_product(self, p, expected):
        plan = _plan(tuple(f"step_{i}" for i in range(6)))
        model = _model_with_probability(tuple(f"step_{i}" for i in range(6)), p)
        trajectory = _trajectory(model, plan)
        assessment = RiskEngine().assess(trajectory)
        assert assessment.probability_of_success == pytest.approx(expected, rel=1e-6)


class TestSequentialDependencyModel:
    """Task Test 8: multi-step probability respects the ONLY dependency
    structure that actually exists for a kernel/pipeline/prediction/ plan
    -- a flat, ordered sequence where every step must individually
    succeed. (PlanStep.preconditions exists but is confirmed, by
    exhaustive grep, never read anywhere in this subsystem; there is no
    ExecutionGraph/DAG here -- see the plan's Context section. Making
    prediction ExecutionGraph-aware would require Plan generation changes,
    explicitly out of scope for this pass; this test locks in and
    documents the current, correct-for-flat-plans behavior rather than
    silently assuming independence.)"""

    def test_one_low_probability_step_drags_down_the_whole_sequence(self):
        plan = _plan(("good_a", "bad", "good_b"))
        model = TransitionModel(
            known_transitions={
                ("", "good_a"): (WorldTransition(description="ok", probability=0.99, confidence=0.9),),
                ("", "bad"): (WorldTransition(description="bad", probability=0.01, confidence=0.9),),
                ("", "good_b"): (WorldTransition(description="ok", probability=0.99, confidence=0.9),),
            }
        )
        trajectory = _trajectory(model, plan)
        assessment = RiskEngine().assess(trajectory)
        # A single near-certain-failure step dominates the joint probability,
        # exactly as a genuine sequential AND-chain (each step required)
        # should behave -- not averaged/diluted by the two good steps.
        assert assessment.probability_of_success < 0.02


class TestColdStartUnchanged:
    """Task Test 9: no-history prediction still uses the documented,
    pre-existing cold-start behavior (regression -- not modified by this
    pass, per the explicit "do not change the learning model" instruction)."""

    def test_unknown_action_defaults_to_honest_half_probability(self):
        plan = _plan(("never_seen_before",))
        model = TransitionModel()  # empty -- no learned history at all
        trajectory = _trajectory(model, plan)
        applied = trajectory.states[-1].applied_transition
        assert applied.kind == TransitionKind.UNKNOWN
        assert applied.probability == 0.5


class TestReproducibility:
    """Task Test 10: identical deterministic inputs produce an identical
    prediction decision."""

    def test_same_inputs_produce_same_recommendation_twice(self):
        plan = _plan(("step_a", "step_b"))
        model = _model_with_probability(("step_a", "step_b"), 0.8)

        def run_once():
            cf_engine = CounterfactualEngine(model)
            baseline = cf_engine.simulate_baseline(None, None, plan)
            scenarios = scenarios_from_counterfactuals("Baseline", baseline, ())
            return ScenarioEvaluator().evaluate_and_recommend(scenarios)

        result_1 = run_once()
        result_2 = run_once()
        assert result_1.recommendation == result_2.recommendation
        assert result_1.candidates[0].probability == pytest.approx(result_2.candidates[0].probability)


class TestActorScopedHistoryDivergence:
    """Task Test 11: different actor learned histories produce different
    predictions for the same plan -- actor-scoped learning genuinely
    affects the outcome (not silently ignored/flattened)."""

    def test_two_different_transition_models_diverge(self):
        plan = _plan(("step_a",))
        actor_a_model = _model_with_probability(("step_a",), 0.9)  # actor A: good history
        actor_b_model = _model_with_probability(("step_a",), 0.1)  # actor B: poor history

        traj_a = _trajectory(actor_a_model, plan)
        traj_b = _trajectory(actor_b_model, plan)
        assessment_a = RiskEngine().assess(traj_a)
        assessment_b = RiskEngine().assess(traj_b)

        assert assessment_a.probability_of_success != pytest.approx(assessment_b.probability_of_success)
        assert assessment_a.probability_of_success > assessment_b.probability_of_success


class TestFreshPlanReachesAndIsEvaluatedByPrediction:
    """Task Test 12: a fresh plan (exactly what the plan-hysteresis fix
    now lets through) is genuinely evaluated by prediction -- not skipped,
    not defaulted -- via the real, live PredictionIntegratedPolicy wiring,
    with real provenance/prediction_id/participation now populated
    (closing the two concrete gaps this pass found: PredictionResult
    .prediction_id was declared but never set, and Prediction.provenance
    was always left all-default/empty)."""

    def test_live_wiring_produces_a_real_populated_prediction(self):
        plan = _plan(("step_a", "step_b"), goal="find the nearest pharmacy")
        model = TransitionModel(
            known_transitions={
                ("find nearest pharmacy", "step_a"): (WorldTransition(probability=0.9, confidence=0.9),),
                ("find nearest pharmacy", "step_b"): (WorldTransition(probability=0.9, confidence=0.9),),
            }
        )
        assumption = CounterfactualAssumption(description="Pharmacy closed")
        policy = PredictionIntegratedPolicy(transition_model=model, counterfactual_assumptions=(assumption,))

        async def noop(state):
            return state

        policy.configure(
            observe=noop,
            believe=noop,
            plan=noop,
            execute=noop,
            observe_outcome=noop,
            learn=noop,
            compile_phi=noop,
            predict=noop,
            commit=noop,
        )
        predict_stage = dict(policy._stages)["predict"]

        actor = Actor(actor_id="arjun_fresh", tenant_id="acme")
        belief = BeliefState(actor_id="arjun_fresh", tenant_id="acme")
        belief.plan = plan
        state = CognitiveState(actor=actor, belief=belief)
        state.metrics = {"execution_id": "exec-fresh-1"}

        result_state = asyncio.run(predict_stage(state))
        pr = result_state.prediction_result

        assert pr["prediction_id"] != ""  # was always "" before this pass
        assert pr["candidates"][0]["prediction"]["provenance"]["actor_id"] == "arjun_fresh"  # was always "" before
        assert pr["candidates"][0]["prediction"]["provenance"]["run_id"] == "exec-fresh-1"
        assert len(pr["candidates"]) == 2  # Baseline + the one registered assumption, both real


# ═══════════════════════════════════════════════════════════════════════════
# Follow-up pass: 4 gaps closed (dependency awareness, dead-scaffolding
# wiring, simulate_baseline try/except -- corrected as not a real gap, see
# the plan -- and UI observability, covered on the frontend side).
# ═══════════════════════════════════════════════════════════════════════════


class TestDependencyAwareProbability:
    """Gap 1: PlanStep.depends_on (0-based indices of other steps in the
    same plan that must have succeeded first), threaded through
    SimulationState.depends_on and consumed by RiskEngine._path_probability.
    Three cases: the no-op regression every existing (empty-depends_on)
    plan gets, a declared dependency on a step that failed (probability
    forced to 0.0 -- it genuinely cannot happen), and a declared
    dependency that succeeded (ordinary product, unaffected)."""

    def test_empty_depends_on_is_a_byte_identical_no_op(self):
        """Every plan that exists today has depends_on=() on every step
        (the field's own default) -- this must reproduce the exact same
        product as before depends_on existed at all."""
        plan = _plan(("step_a", "step_b", "step_c"))
        model = _model_with_probability(("step_a", "step_b", "step_c"), 0.9)
        trajectory = _trajectory(model, plan)
        assessment = RiskEngine().assess(trajectory)
        assert assessment.probability_of_success == pytest.approx(0.9**3, rel=1e-9)

    def test_dependency_on_a_failed_step_excludes_the_dependent_steps_own_probability(
        self,
    ):
        """Production Hardening fix (Phase 1D): step 1 ("b") depends on
        step 0 ("a"); "a"'s own registered transition is a near-certain
        failure (0.05). "b" can not meaningfully happen, so its OWN
        contribution to the path probability is excluded (same "no
        informative signal" treatment UNKNOWN already gets) rather than
        multiplied in at its own raw (irrelevant) probability.

        Previously this hard-zeroed the ENTIRE path (asserted 0.0 here)
        instead of just excluding "b" -- a genuine production bug: "a"'s
        own low probability was ALREADY correctly multiplied into the
        product when "a" itself was processed, so the additional hard
        zero was a double penalty that turned one moderately-unlikely
        step into absolute certainty of failure for the whole downstream
        chain. Confirmed live: a single real observed failure
        (probability 0.15) permanently zeroed every later prediction for
        a normal 6-step purchase plan, and a 0% prediction is rejected
        and therefore never re-executed to gather better evidence — a
        real actor got permanently stuck. The correct value here is "a"'s
        own probability alone (0.05), not 0.0 and not the naive product
        with "b"'s irrelevant 0.95."""
        plan = Plan(
            goal="",
            cost=0.0,
            confidence=0.8,
            risk=0.0,
            planner="llm",
            steps=(
                PlanStep(action="a", description="a"),
                PlanStep(action="b", description="b", depends_on=(0,)),
            ),
        )
        model = TransitionModel(
            known_transitions={
                ("", "a"): (WorldTransition(description="a fails", probability=0.05, confidence=0.9),),
                ("", "b"): (WorldTransition(description="b would succeed", probability=0.95, confidence=0.9),),
            }
        )
        trajectory = _trajectory(model, plan)
        assessment = RiskEngine().assess(trajectory)
        assert assessment.probability_of_success == pytest.approx(0.05, abs=1e-9)

    def test_dependency_on_a_succeeded_step_is_an_ordinary_product(self):
        """Same shape as above, but "a" now succeeds (0.9) -- "b"'s
        declared dependency is satisfied, so the path probability is the
        ordinary product of both steps' own probabilities, exactly as if
        depends_on had never been declared."""
        plan = Plan(
            goal="",
            cost=0.0,
            confidence=0.8,
            risk=0.0,
            planner="llm",
            steps=(
                PlanStep(action="a", description="a"),
                PlanStep(action="b", description="b", depends_on=(0,)),
            ),
        )
        model = TransitionModel(
            known_transitions={
                ("", "a"): (WorldTransition(description="a succeeds", probability=0.9, confidence=0.9),),
                ("", "b"): (WorldTransition(description="b succeeds", probability=0.95, confidence=0.9),),
            }
        )
        trajectory = _trajectory(model, plan)
        assessment = RiskEngine().assess(trajectory)
        assert assessment.probability_of_success == pytest.approx(0.9 * 0.95, rel=1e-9)


class TestPolicyDelegationEquivalence:
    """Gap 2: integrated_predict() now delegates to DeterministicPredictionPolicy
    (policies.py) instead of inlining the CounterfactualEngine/
    ScenarioEvaluator sequence directly. Proves the delegation is a
    behavior-preserving refactor -- calling DeterministicPredictionPolicy
    directly (with the same inputs the live wiring supplies) produces the
    same recommendation/candidate-probabilities as going through the full
    PredictionIntegratedPolicy stage."""

    def test_direct_policy_call_matches_live_stage_output(self):
        plan = _plan(("step_a", "step_b"), goal="find the nearest pharmacy")
        model = TransitionModel(
            known_transitions={
                ("find nearest pharmacy", "step_a"): (WorldTransition(probability=0.9, confidence=0.9),),
                ("find nearest pharmacy", "step_b"): (WorldTransition(probability=0.9, confidence=0.9),),
            }
        )
        assumption = CounterfactualAssumption(description="Pharmacy closed")

        from src.monkey_brain.kernel.pipeline.prediction.policies import (
            DeterministicPredictionPolicy,
            PredictionPolicyInput,
        )

        direct_result = DeterministicPredictionPolicy(
            transition_model=model,
            counterfactual_assumptions=(assumption,),
        ).predict(PredictionPolicyInput(plan=plan))

        policy = PredictionIntegratedPolicy(transition_model=model, counterfactual_assumptions=(assumption,))

        async def noop(state):
            return state

        policy.configure(
            observe=noop,
            believe=noop,
            plan=noop,
            execute=noop,
            observe_outcome=noop,
            learn=noop,
            compile_phi=noop,
            predict=noop,
            commit=noop,
        )
        predict_stage = dict(policy._stages)["predict"]

        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        belief.plan = plan
        state = CognitiveState(actor=actor, belief=belief)
        state.metrics = {"execution_id": "exec-1"}
        live_state = asyncio.run(predict_stage(state))
        live_pr = live_state.prediction_result

        # prediction_id/provenance are intentionally NOT compared -- each
        # call mints its own fresh id/timestamp by design (see
        # integration.py's own docstring). Everything that reflects the
        # actual prediction ALGORITHM must match exactly.
        assert live_pr["recommendation"] == direct_result.result.recommendation
        assert len(live_pr["candidates"]) == len(direct_result.result.candidates)
        live_probs = sorted(c["probability"] for c in live_pr["candidates"])
        direct_probs = sorted(c.probability for c in direct_result.result.candidates)
        assert live_probs == pytest.approx(direct_probs, rel=1e-9)
        assert (
            live_pr["metadata"]["scenario_participation"]["aggregation_status"]
            == direct_result.result.metadata["scenario_participation"]["aggregation_status"]
        )
