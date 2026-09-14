"""Regression coverage for the Cross-Goal Plan Contamination fix.

Before this fix, plan hysteresis state (kernel/pipeline/comparison/
integration.py::ComparisonIntegratedPolicy._current_plan,
kernel/pipeline/planning/current_plan_store.py's Redis persistence,
belief.metadata's skip-replan counters, and kernel/pipeline/prediction/
transitions.py::TransitionModel.known_transitions) was keyed by actor_id
alone. A standing plan/score-floor/failure-history belonging to one goal
could therefore suppress a freshly generated plan for a completely
unrelated goal -- confirmed live: an actor's already-failing grocery plan
silently executed in place of a genuinely fresh ~21s LLM-generated pharmacy
plan for "find the nearest pharmacy."

Every test here drives the REAL `_run_decide` (kernel/pipeline/comparison/
integration.py) and `TransitionModel`/`TransitionPredictionEngine`
(kernel/pipeline/prediction/transitions.py) directly -- both are near-pure
pipeline-stage code (see plan_hysteresis.py's own "pure logic, no I/O"
docstring), so these tests construct real dataclasses (CognitiveState,
Actor, BeliefState, belief_state.Plan) and call the stage function
directly, matching test_prediction_integration.py's and
test_planning_scoring.py's existing conventions -- no mocks, no Redis
(current_plan_store.py's Redis calls are non-fatal no-ops without a live
Redis, confirmed; these tests exercise policy._current_plans / state.metrics
directly instead).

Per this repo's standing session convention, this file is written but not
executed by the assistant. Run with:
    python -m pytest tests/unit/test_plan_hysteresis_goal_scoping.py -v
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.belief_state import BeliefState, Plan, PlanStep
from src.monkey_brain.kernel.pipeline.actor import Actor
from src.monkey_brain.kernel.pipeline.comparison.integration import (
    ComparisonIntegratedPolicy,
    _run_decide,
)
from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
    CurrentPlanRecord,
    plan_to_dict,
)
from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal
from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionModel,
    TransitionPredictionEngine,
)


def _plan(goal: str, *, steps: tuple[str, ...] = ("Payment",)) -> Plan:
    return Plan(
        goal=goal,
        steps=tuple(PlanStep(action=s, description=s) for s in steps),
        cost=0.0,
        confidence=0.8,
        risk=0.0,
        planner="llm",
    )


def _prediction_result(probability: float, expected_utility: float) -> dict:
    return {
        "selected": {
            "probability": probability,
            "prediction": {"expected_utility": expected_utility},
        }
    }


def _state(*, plan: Plan | None, prediction_result: dict | None, belief_goal: str = "") -> CognitiveState:
    actor = Actor(actor_id="arjun", tenant_id="acme")
    belief = BeliefState(actor_id="arjun", tenant_id="acme")
    if belief_goal:
        belief.update_goal(name=belief_goal)
    state = CognitiveState(actor=actor, belief=belief)
    state.plan = plan
    state.prediction_result = prediction_result
    state.metrics = {}
    return state


def _record(goal: str, *, score: float, plan_id: str = "standing") -> CurrentPlanRecord:
    # A real, non-empty serialized plan -- _run_decide's "keep" branch
    # early-returns without swapping state.plan when current.plan is falsy
    # (nothing to reconstruct), so a genuinely non-empty dict here is
    # required for tests that need to observe the actual swap-to-standing-
    # plan behavior, not just the decision metrics.
    standing_plan = _plan(goal)
    return CurrentPlanRecord(
        plan_id=plan_id,
        actor_id="arjun",
        goal=goal,
        steps=("Payment",),
        step_descriptions=("Pay",),
        score=score,
        plan=plan_to_dict(standing_plan),
    )


class TestCrossGoalContaminationFixed:
    """Property 1 / task Test 2: a standing plan for Goal A must not
    suppress a freshly generated plan for unrelated Goal B -- the
    candidate is not rejected for failing to beat Goal A's score."""

    def test_pharmacy_plan_survives_despite_failing_grocery_standing_plan(self):
        policy = ComparisonIntegratedPolicy()
        grocery_key = canonicalize_goal("buy groceries")
        policy._current_plans[grocery_key] = _record("buy groceries", score=-0.9)

        pharmacy_plan = _plan("get medicine")
        state = _state(plan=pharmacy_plan, prediction_result=_prediction_result(0.6, 0.5))

        result = asyncio.run(_run_decide(state, policy))

        assert result.plan is pharmacy_plan
        assert result.metrics["decide_action"] == "replace"
        assert result.metrics["decide_goal_key"] == canonicalize_goal("get medicine")


class TestNewGoalBecomesItsOwnStandingPlan:
    """Task Test 3: the Goal B candidate that wins doesn't just execute
    once -- it becomes the real, persisted standing plan FOR GOAL B
    specifically (not overwriting or merging with Goal A's own entry)."""

    def test_pharmacy_candidate_registers_under_its_own_goal_key(self):
        policy = ComparisonIntegratedPolicy()
        grocery_key = canonicalize_goal("buy groceries")
        pharmacy_key = canonicalize_goal("get medicine")
        policy._current_plans[grocery_key] = _record("buy groceries", score=-0.9, plan_id="grocery-standing")

        pharmacy_plan = _plan("get medicine")
        state = _state(plan=pharmacy_plan, prediction_result=_prediction_result(0.6, 0.5))
        result = asyncio.run(_run_decide(state, policy))

        new_plan_id = result.metrics["decide_new_plan_id"]
        assert new_plan_id is not None
        # Goal B now has its OWN entry, under its OWN key...
        assert policy._current_plans[pharmacy_key].plan_id == new_plan_id
        assert canonicalize_goal(policy._current_plans[pharmacy_key].goal) == pharmacy_key
        # ...and Goal A's own standing plan is completely untouched.
        assert policy._current_plans[grocery_key].plan_id == "grocery-standing"


class TestSameGoalHysteresisPreserved:
    """Property 2 / task Test 1: same-goal hysteresis is unweakened by
    this fix -- the existing keep-vs-replace policy still applies exactly
    as before when standing and candidate share a goal."""

    def test_weaker_same_goal_candidate_is_kept_out(self):
        policy = ComparisonIntegratedPolicy()
        goal_key = canonicalize_goal("buy groceries")
        policy._current_plans[goal_key] = _record("buy groceries", score=0.8)

        weak_plan = _plan("buy groceries")
        state = _state(plan=weak_plan, prediction_result=_prediction_result(0.5, 0.1))

        result = asyncio.run(_run_decide(state, policy))

        assert result.metrics["decide_action"] == "keep"
        assert result.plan is not weak_plan
        assert result.plan.goal == "buy groceries"


class TestSameGoalClearlySuperiorCandidateReplaces:
    """Task Test 4: same goal, a candidate clearly (>10% hysteresis
    margin) better than the standing plan -- must replace it."""

    def test_clearly_superior_same_goal_candidate_replaces_standing(self):
        policy = ComparisonIntegratedPolicy()
        goal_key = canonicalize_goal("buy groceries")
        # standing score = 0.5*0.8 + 0.3*0.0 = 0.4
        policy._current_plans[goal_key] = _record("buy groceries", score=0.4)

        # candidate score = 0.5*1.0 + 0.3*1.0 = 0.8 -- a 100% improvement,
        # far above the 10% margin.
        strong_plan = _plan("buy groceries")
        state = _state(plan=strong_plan, prediction_result=_prediction_result(1.0, 1.0))

        result = asyncio.run(_run_decide(state, policy))

        assert result.metrics["decide_action"] == "replace"
        assert result.plan is strong_plan
        assert policy._current_plans[goal_key].plan_id == result.metrics["decide_new_plan_id"]


class TestSameGoalMarginallyBetterCandidateSuppressed:
    """Task Test 5: same goal, a candidate only marginally better than the
    standing plan (below the configured 10% hysteresis margin) -- the
    standing plan must remain, exactly as the existing policy intends
    (don't thrash on marginal improvements)."""

    def test_marginally_better_same_goal_candidate_is_suppressed(self):
        policy = ComparisonIntegratedPolicy()
        goal_key = canonicalize_goal("buy groceries")
        # standing score = 0.5*0.8 + 0.3*0.0 = 0.4
        policy._current_plans[goal_key] = _record("buy groceries", score=0.4)

        # candidate score = 0.5*0.82 + 0.3*0.0 = 0.41 -- a 2.5% improvement,
        # below the default 10% margin (plan_hysteresis.hysteresis_margin()).
        marginal_plan = _plan("buy groceries")
        state = _state(plan=marginal_plan, prediction_result=_prediction_result(0.82, 0.0))

        result = asyncio.run(_run_decide(state, policy))

        assert result.metrics["decide_action"] == "keep"
        assert result.plan is not marginal_plan
        assert policy._current_plans[goal_key].plan_id == "standing"  # untouched


class TestNoStandingPlanBootstrap:
    """Task Test 6: no standing plan exists anywhere (truly empty policy,
    not just a different goal) -- the fresh candidate is accepted
    unconditionally, the pre-existing bootstrap case."""

    def test_fresh_candidate_accepted_when_policy_has_no_state_at_all(self):
        policy = ComparisonIntegratedPolicy()
        assert policy._current_plans == {}

        plan = _plan("buy groceries")
        state = _state(plan=plan, prediction_result=_prediction_result(0.5, 0.0))

        result = asyncio.run(_run_decide(state, policy))

        assert result.metrics["decide_action"] == "replace"
        assert result.plan is plan


class TestNewGoalCleanState:
    """Property 3: a brand-new goal has no state to inherit."""

    def test_first_time_goal_has_no_existing_plan_entry(self):
        policy = ComparisonIntegratedPolicy()
        grocery_key = canonicalize_goal("buy groceries")
        policy._current_plans[grocery_key] = _record("buy groceries", score=0.9)

        pharmacy_key = canonicalize_goal("get medicine")
        assert policy._current_plans.get(pharmacy_key) is None

        pharmacy_plan = _plan("get medicine")
        state = _state(plan=pharmacy_plan, prediction_result=_prediction_result(0.7, 0.3))
        result = asyncio.run(_run_decide(state, policy))

        assert result.metrics["decide_action"] == "replace"
        assert result.metrics["decide_current_plan_id"] is None  # no standing plan existed for this goal


class TestFailureHistoryIsolation:
    """Property 4 / task Test 7: a fresh candidate with extremely low
    learned probability but a materially different goal must not be
    blocked by an unrelated standing plan/failure history -- TransitionModel
    failure history for one goal doesn't leak into another goal's lookup
    for the same action name."""

    def test_pharmacy_action_lookup_unaffected_by_grocery_failures(self):
        model = TransitionModel()
        for _ in range(5):
            model = model.learn_from_execution(
                "Payment",
                success=False,
                confidence=0.9,
                goal_key="buy groceries",
            )
        grocery_prob = model.known_transitions[("buy groceries", "Payment")][-1].probability
        assert grocery_prob < 0.3  # genuinely poisoned for groceries

        engine = TransitionPredictionEngine(model)

        class _Action:
            action = "Payment"

        pharmacy_transitions = engine.predict_transitions(None, None, _Action(), goal_key="get medicine")
        assert pharmacy_transitions[0].probability == 0.5  # honest UNKNOWN default, not inherited


class TestReverseDirection:
    """Property 5: the fix is symmetric, not a pharmacy/grocery special
    case -- seed pharmacy as standing, grocery as the fresh candidate."""

    def test_grocery_plan_survives_despite_failing_pharmacy_standing_plan(self):
        policy = ComparisonIntegratedPolicy()
        pharmacy_key = canonicalize_goal("get medicine")
        policy._current_plans[pharmacy_key] = _record("get medicine", score=-0.9)

        grocery_plan = _plan("buy groceries")
        state = _state(plan=grocery_plan, prediction_result=_prediction_result(0.6, 0.5))

        result = asyncio.run(_run_decide(state, policy))

        assert result.plan is grocery_plan
        assert result.metrics["decide_action"] == "replace"


class TestActorIsolation:
    """Property 6 / task Test 8: Actor A's standing plan must not suppress
    Actor B's fresh plan -- two actors with the same goal_key never share
    state --
    each ComparisonIntegratedPolicy instance owns its own _current_plans,
    and Redis persistence (current_plan_store.py) keys on actor_id too."""

    def test_two_actors_same_goal_have_independent_current_plans(self):
        policy_a = ComparisonIntegratedPolicy()
        policy_b = ComparisonIntegratedPolicy()
        goal_key = canonicalize_goal("buy groceries")
        policy_a._current_plans[goal_key] = _record("buy groceries", score=0.9, plan_id="actor-a-plan")

        assert policy_b._current_plans.get(goal_key) is None  # actor B's policy is a separate instance/dict

        candidate = _plan("buy groceries")
        state_b = _state(plan=candidate, prediction_result=_prediction_result(0.5, 0.1))
        state_b.actor.actor_id = "someone_else"
        result = asyncio.run(_run_decide(state_b, policy_b))

        assert result.metrics["decide_action"] == "replace"  # actor B bootstraps fresh, unaffected by actor A


class TestCrossGoalInvariantAssertion:
    """The runtime invariant itself: it must be structurally impossible,
    not merely unlikely, for a stored record's goal_key to mismatch the
    candidate's. Simulates a corrupted/mis-keyed dict entry directly
    (bypassing the normal keyed-write path) to prove the assertion fires."""

    def test_mismatched_goal_key_entry_raises(self):
        policy = ComparisonIntegratedPolicy()
        wrong_key = canonicalize_goal("get medicine")
        # Deliberately mis-file a grocery record under the pharmacy key --
        # this should never happen via normal writes (which always key by
        # the record's own canonicalized goal), so this proves the
        # invariant assertion, not normal application behavior.
        policy._current_plans[wrong_key] = _record("buy groceries", score=0.9)

        pharmacy_plan = _plan("get medicine")
        state = _state(plan=pharmacy_plan, prediction_result=_prediction_result(0.5, 0.1))

        with pytest.raises(AssertionError, match="Cross-goal plan contamination"):
            asyncio.run(_run_decide(state, policy))


class TestOriginalFailureReproduces:
    """Reproduces the original live failure in miniature: a standing
    grocery plan with a poisoned (very negative) score, a fresh pharmacy
    plan with a normal (non-poisoned) prediction. Proves the fresh plan
    now survives -- the actual code path that broke, not a rewrite of it."""

    def test_fresh_pharmacy_plan_survives_poisoned_grocery_standing_plan(self):
        policy = ComparisonIntegratedPolicy()
        grocery_key = canonicalize_goal("find the best grocery deals")
        # Mirrors the real observed values: probability~1.15e-05,
        # expected_utility~-0.9999769900244257.
        policy._current_plans[grocery_key] = _record(
            "find the best grocery deals",
            score=(0.5 * 1.15e-05 + 0.3 * -0.9999769900244257),
        )

        pharmacy_plan = _plan("find the nearest pharmacy", steps=("LocateNearestStore", "Navigate"))
        state = _state(
            plan=pharmacy_plan,
            prediction_result=_prediction_result(0.9, 0.8),  # a normal, non-poisoned prediction
        )

        result = asyncio.run(_run_decide(state, policy))

        assert result.plan is pharmacy_plan
        assert result.metrics["decide_action"] == "replace"
        assert result.metrics["decide_reason"] != "Baseline 0% (rejected)."


# ──────────────────────────────────────────────────────────────
# PRODUCTION HARDENING — Broken Plan Cache (Phase 1C)
#
# Regression target: a Current Plan whose last real execution FAILED
# used to keep winning "keep" against fresh candidates purely on
# predicted-probability/utility score (score_plan has no notion of
# actual execution outcome) -- confirmed live, a plan missing
# OrderCreation replayed identically across 3 consecutive retries with
# 0/2 successes each time, because a fresh candidate rarely clears the
# 10% hysteresis margin over an already-cached plan. Fixed two layers:
# (1) _run_decide bypasses the score comparison entirely and force-
# replaces when current.last_execution_failed is True; (2) the
# _record_plan_outcome_feedback stage (new pipeline stage, runs right
# after observe_outcome) sets that flag from the real, just-executed
# outcome so the NEXT tick's _run_decide can see it.
# ──────────────────────────────────────────────────────────────


def _outcome_state(*, goal_key: str, actions_executed: int, failure_count: int, goal_achieved: bool) -> CognitiveState:
    state = _state(plan=None, prediction_result=None)
    state.metrics["decide_goal_key"] = goal_key
    state.outcome = {
        "actions_executed": actions_executed,
        "success_count": actions_executed - failure_count,
        "failure_count": failure_count,
        "goal_achieved": goal_achieved,
    }
    return state


class TestFailedStandingPlanBypassesHysteresis:
    """A Current Plan already marked last_execution_failed must be
    replaced by a fresh candidate unconditionally -- even one that
    scores WORSE than the failed plan's own (stale, pre-failure) score.
    A demonstrably broken plan has forfeited hysteresis protection; it
    must never win "keep" again on the strength of an old score."""

    def test_failed_standing_plan_is_replaced_even_by_a_weaker_candidate(self):
        policy = ComparisonIntegratedPolicy()
        goal_key = canonicalize_goal("buy groceries")
        record = _record("buy groceries", score=0.9)  # high score, but...
        policy._current_plans[goal_key] = replace(record, last_execution_failed=True)

        weak_plan = _plan("buy groceries")
        state = _state(plan=weak_plan, prediction_result=_prediction_result(0.1, 0.0))  # low score

        result = asyncio.run(_run_decide(state, policy))

        assert result.metrics["decide_action"] == "replace"
        assert result.plan is weak_plan
        assert "last execution failed" in result.metrics["decide_reason"].lower()

    def test_healthy_standing_plan_still_protected_by_normal_hysteresis(self):
        """Sanity check the fix didn't turn EVERY keep into a replace:
        last_execution_failed defaults to False, and a marginally-better
        candidate against a healthy standing plan is still suppressed
        exactly as TestSameGoalMarginallyBetterCandidateSuppressed
        already covers for the non-failed case."""
        policy = ComparisonIntegratedPolicy()
        goal_key = canonicalize_goal("buy groceries")
        record = _record("buy groceries", score=0.9)
        assert record.last_execution_failed is False
        policy._current_plans[goal_key] = record

        weak_plan = _plan("buy groceries")
        state = _state(plan=weak_plan, prediction_result=_prediction_result(0.1, 0.0))

        result = asyncio.run(_run_decide(state, policy))

        assert result.metrics["decide_action"] == "keep"


class TestPlanOutcomeFeedback:
    """Unit coverage for _record_plan_outcome_feedback itself — the
    stage that sets last_execution_failed from a real outcome, not just
    its downstream effect on _run_decide above."""

    def test_failed_execution_marks_the_standing_plan_failed(self):
        from src.monkey_brain.kernel.pipeline.comparison.integration import (
            _record_plan_outcome_feedback,
        )

        policy = ComparisonIntegratedPolicy()
        goal_key = canonicalize_goal("buy groceries")
        policy._current_plans[goal_key] = _record("buy groceries", score=0.5)
        state = _outcome_state(goal_key=goal_key, actions_executed=6, failure_count=6, goal_achieved=False)

        _record_plan_outcome_feedback(state, policy)

        assert policy._current_plans[goal_key].last_execution_failed is True

    def test_successful_execution_clears_a_previously_failed_flag(self):
        from src.monkey_brain.kernel.pipeline.comparison.integration import (
            _record_plan_outcome_feedback,
        )

        policy = ComparisonIntegratedPolicy()
        goal_key = canonicalize_goal("buy groceries")
        policy._current_plans[goal_key] = replace(
            _record("buy groceries", score=0.5),
            last_execution_failed=True,
        )
        state = _outcome_state(goal_key=goal_key, actions_executed=6, failure_count=0, goal_achieved=True)

        _record_plan_outcome_feedback(state, policy)

        assert policy._current_plans[goal_key].last_execution_failed is False

    def test_zero_action_tick_does_not_mark_the_plan_failed(self):
        """A no-op tick (nothing executed) says nothing about whether
        the plan itself works -- must not be misread as a failure."""
        from src.monkey_brain.kernel.pipeline.comparison.integration import (
            _record_plan_outcome_feedback,
        )

        policy = ComparisonIntegratedPolicy()
        goal_key = canonicalize_goal("buy groceries")
        policy._current_plans[goal_key] = _record("buy groceries", score=0.5)
        state = _outcome_state(goal_key=goal_key, actions_executed=0, failure_count=0, goal_achieved=False)

        _record_plan_outcome_feedback(state, policy)

        assert policy._current_plans[goal_key].last_execution_failed is False

    def test_partial_success_still_marks_failed(self):
        """A majority-failing episode (e.g. 1/6) is a real failure
        signal, not a pass — matches the exact live repro (cheese plan,
        1/6 success) that motivated this fix."""
        from src.monkey_brain.kernel.pipeline.comparison.integration import (
            _record_plan_outcome_feedback,
        )

        policy = ComparisonIntegratedPolicy()
        goal_key = canonicalize_goal("buy groceries")
        policy._current_plans[goal_key] = _record("buy groceries", score=0.5)
        state = _outcome_state(goal_key=goal_key, actions_executed=6, failure_count=5, goal_achieved=False)

        _record_plan_outcome_feedback(state, policy)

        assert policy._current_plans[goal_key].last_execution_failed is True
