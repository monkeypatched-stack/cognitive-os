"""Tests for planning trace & explainability (Step 8.8).

Validates PlanningTrace construction (build_planning_trace), its .explain()
narrative (the Goal -> Decompose -> Generate Candidates -> Validate -> Score
-> Select format from Step 8's own acceptance example), .to_dict()
serializability, and IntegratedPlanningEngine's attachment of the trace to
every Plan produced (plan_with_trace() and metadata["planning_trace"]).

Includes a regression test for a bug caught while building this: the trace
must reflect every candidate's actual score, not just the winner's — an
earlier version passed the pre-scoring candidate list into the trace,
leaving every candidate looking "unscored" even though scoring had run.
"""

from __future__ import annotations

import json

from src.monkey_brain.kernel.pipeline.belief_state import (
    BeliefState,
    Goal as BeliefGoal,
    Plan,
)
from src.monkey_brain.kernel.pipeline.planning.domain import Goal, PlanCandidate
from src.monkey_brain.kernel.pipeline.planning.decomposition import GoalDecomposer
from src.monkey_brain.kernel.pipeline.planning.candidates import CandidateGenerator
from src.monkey_brain.kernel.pipeline.planning.scoring import PlanScorer
from src.monkey_brain.kernel.pipeline.planning.trace import (
    PlanningTrace,
    build_planning_trace,
)
from src.monkey_brain.kernel.pipeline.planning.integration import (
    IntegratedPlanningEngine,
)
from src.monkey_brain.kernel.pipeline.planning.domain import PlanningConstraint


def _acquire_milk_scored_candidates() -> tuple[Goal, tuple[PlanCandidate, ...]]:
    goal = Goal(name="acquire_milk", description="Acquire 2L whole milk")
    candidates = CandidateGenerator().generate(goal)
    scored = PlanScorer().score_candidates(candidates)
    return goal, scored


# ═══════════════════════════════════════════════════════════════════════════
# build_planning_trace — aggregation
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildPlanningTrace:
    def test_all_candidates_recorded(self):
        goal, scored = _acquire_milk_scored_candidates()
        best = max(scored, key=lambda c: c.score)

        trace = build_planning_trace(goal, None, scored, best)

        assert len(trace.candidate_plans) == 3
        assert trace.selected_plan is best.plan

    def test_regression_trace_reflects_actual_scores_not_unscored(self):
        """The bug: passing pre-scoring candidates into the trace made every
        candidate look unscored even though scoring had already run."""
        goal, scored = _acquire_milk_scored_candidates()
        best = max(scored, key=lambda c: c.score)

        trace = build_planning_trace(goal, None, scored, best)

        assert all(c.score is not None for c in trace.candidate_plans)
        assert "unscored" not in trace.explain()

    def test_rejected_plans_is_the_rejected_subset(self):
        goal, scored = _acquire_milk_scored_candidates()
        rejected_one = scored[0].__class__(
            candidate_id=scored[0].candidate_id,
            plan=scored[0].plan,
            score=scored[0].score,
            rejected=True,
            rejection_reason="too expensive",
        )
        candidates = (rejected_one,) + scored[1:]

        trace = build_planning_trace(goal, None, candidates, scored[1])

        assert trace.rejected_plans == (rejected_one,)
        assert "too expensive" in trace.validation_failures[0]

    def test_no_candidates_produces_empty_trace_with_rationale(self):
        goal = Goal(name="never_registered")
        trace = build_planning_trace(goal, None, (), None)
        assert trace.candidate_plans == ()
        assert "No candidates were generated" in trace.rationale

    def test_all_rejected_produces_none_selected_plan(self):
        goal, scored = _acquire_milk_scored_candidates()
        all_rejected = tuple(
            c.__class__(
                candidate_id=c.candidate_id,
                plan=c.plan,
                score=c.score,
                rejected=True,
                rejection_reason="x",
            )
            for c in scored
        )
        trace = build_planning_trace(goal, None, all_rejected, None)
        assert trace.selected_plan is None
        assert "rejected" in trace.rationale.lower()

    def test_custom_rationale_overrides_default(self):
        goal, scored = _acquire_milk_scored_candidates()
        trace = build_planning_trace(goal, None, scored, scored[0], rationale="custom explanation")
        assert trace.rationale == "custom explanation"


# ═══════════════════════════════════════════════════════════════════════════
# .explain() — the Goal -> Decompose -> ... -> Select narrative
# ═══════════════════════════════════════════════════════════════════════════


class TestExplain:
    def test_matches_acceptance_example_structure(self):
        """Step 8's acceptance criteria shows: Goal / Decompose / Generate
        Candidates / Validate / Score / Select as the expected narrative
        shape for exactly this scenario."""
        goal, scored = _acquire_milk_scored_candidates()
        best = max(scored, key=lambda c: c.score)
        trace = build_planning_trace(goal, None, scored, best)

        narrative = trace.explain()
        lines = narrative.split("\n")

        assert lines[0].startswith("Goal:")
        assert any(line.startswith("Decompose:") for line in lines)
        assert any(line.startswith("Generate Candidates:") for line in lines)
        assert any(line.startswith("Validate:") for line in lines)
        assert any(line.startswith("Score:") for line in lines)
        assert any(line.startswith("Select:") for line in lines)
        assert any(line.startswith("Rationale:") for line in lines)

    def test_all_three_strategies_named(self):
        goal, scored = _acquire_milk_scored_candidates()
        best = max(scored, key=lambda c: c.score)
        narrative = build_planning_trace(goal, None, scored, best).explain()

        for strategy in ("walk_to_store", "drive_to_store", "delivery_service"):
            assert strategy in narrative

    def test_rejected_candidates_marked_with_x(self):
        goal, scored = _acquire_milk_scored_candidates()
        rejected_first = scored[0].__class__(
            candidate_id=scored[0].candidate_id,
            plan=scored[0].plan,
            score=scored[0].score,
            rejected=True,
            rejection_reason="over budget",
        )
        candidates = (rejected_first,) + scored[1:]
        narrative = build_planning_trace(goal, None, candidates, scored[1]).explain()

        assert "✗" in narrative
        assert "✓" in narrative

    def test_no_candidates_still_produces_a_narrative(self):
        goal = Goal(name="never_registered")
        narrative = build_planning_trace(goal, None, (), None).explain()
        assert "Goal: never_registered" in narrative
        assert "Generate Candidates: (none" in narrative


# ═══════════════════════════════════════════════════════════════════════════
# .to_dict() — serializability
# ═══════════════════════════════════════════════════════════════════════════


class TestToDict:
    def test_json_serializable(self):
        goal, scored = _acquire_milk_scored_candidates()
        best = max(scored, key=lambda c: c.score)
        trace = build_planning_trace(goal, None, scored, best)

        d = trace.to_dict()
        json.dumps(d)  # must not raise

    def test_dict_contains_all_deliverable_components(self):
        """goal, candidate plans, rejected plans (via each candidate's
        'rejected' flag), validation failures, selected plan, rationale —
        every component the spec names."""
        goal, scored = _acquire_milk_scored_candidates()
        best = max(scored, key=lambda c: c.score)
        d = build_planning_trace(goal, None, scored, best).to_dict()

        assert d["goal"]["name"] == "acquire_milk"
        assert len(d["candidates"]) == 3
        assert all("rejected" in c for c in d["candidates"])
        assert "validation_failures" in d
        assert d["selected_strategy"] is not None
        assert d["rationale"]


# ═══════════════════════════════════════════════════════════════════════════
# IntegratedPlanningEngine — trace attachment
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegratedPlanningEngineTraceAttachment:
    def test_plan_with_trace_returns_both(self):
        engine = IntegratedPlanningEngine()
        belief = BeliefState(actor_id="alice")
        plan, trace = engine.plan_with_trace(belief, BeliefGoal(name="acquire_milk"))

        assert isinstance(plan, Plan)
        assert isinstance(trace, PlanningTrace)
        assert plan.metadata["strategy"] == trace.selected_plan.metadata["strategy"]

    def test_plan_metadata_includes_serialized_trace(self):
        engine = IntegratedPlanningEngine()
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, BeliefGoal(name="acquire_milk"))

        assert "planning_trace" in plan.metadata
        json.dumps(plan.metadata["planning_trace"])  # must not raise

    def test_fallback_path_still_produces_a_trace(self):
        engine = IntegratedPlanningEngine()
        belief = BeliefState(actor_id="alice")
        plan, trace = engine.plan_with_trace(belief, BeliefGoal(name="never_registered"))

        assert trace.candidate_plans == ()
        assert trace.selected_plan is None
        assert "planning_trace" in plan.metadata

    def test_rejection_path_trace_shows_rejected_candidates(self):
        engine = IntegratedPlanningEngine(
            default_constraints=(PlanningConstraint(kind="budget", parameters={"max_cost": 0.3}, hard=True),)
        )
        belief = BeliefState(actor_id="alice")
        plan, trace = engine.plan_with_trace(belief, BeliefGoal(name="acquire_milk"))

        assert len(trace.rejected_plans) == 2  # drive_to_store, delivery_service
        assert len(trace.validation_failures) == 2
        assert trace.selected_plan.metadata["strategy"] == "walk_to_store"

    def test_plain_plan_still_works_unchanged(self):
        """.plan() (the required Protocol method) must still return only a
        Plan — trace attachment must not change its signature or type."""
        engine = IntegratedPlanningEngine()
        belief = BeliefState(actor_id="alice")
        plan = engine.plan(belief, BeliefGoal(name="acquire_milk"))
        assert isinstance(plan, Plan)
