"""Planning engine integration (Step 8.7) — the formal model behind the
existing PlanningEngine Protocol.

IntegratedPlanningEngine satisfies planner.py's PlanningEngine Protocol
exactly:

    plan = planning_engine.plan(belief_state, goal, runtime_context)

— the same call CognitiveRuntime._generate_plan already makes, using the
same belief_state.BeliefState / belief_state.Goal / belief_state.Plan types
LLMPlanner (kernel/pipeline/llm_planner.py) already uses. It is injected
the same way LLMPlanner is (CognitiveRuntime(planning_engine=...)) — no
changes to belief_runtime.py, orchestrator.py, or compiler.py.

Internally, .plan() runs the full formal pipeline built in Steps 8.1-8.6:

    Goal -> Decompose -> Generate Candidates -> Validate -> Score -> Select
        -> (converted back to) belief_state.Plan

This is a safe drop-in PlanningEngine, not that it produces identical
output to any other implementation: a goal with no registered
CandidateGenerator strategy has no formal candidates to select from, so
.plan() falls back to a real LLMPlanner instance for that goal, same as it
would have without this engine installed at all.

Step 8.8 adds a PlanningTrace of that whole decision, attached (as a plain
dict, via .to_dict()) to the returned Plan.metadata["planning_trace"] — no
change to Plan's fields, so the runtime API stays exactly as it was after
Step 8.7. plan_with_trace() is an additive convenience for callers who want
the rich PlanningTrace object itself (debugging, future visualization)
without depending on metadata's dict shape; .plan() (the required Protocol
method) is unchanged and delegates to it.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from typing import Any

from src.monkey_brain.kernel.pipeline.belief_state import (
    BeliefState,
    Goal,
    Plan,
    PlanStep,
)
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
from src.monkey_brain.kernel.pipeline.planning.domain import (
    Goal as DomainGoal,
    PlanCandidate,
    PlanningConstraint,
)
from src.monkey_brain.kernel.pipeline.planning.decomposition import (
    GoalDecomposer,
    GoalTree,
)
from src.monkey_brain.kernel.pipeline.planning.candidates import CandidateGenerator
from src.monkey_brain.kernel.pipeline.planning.constraints import ConstraintEngine
from src.monkey_brain.kernel.pipeline.planning.scoring import PlanScorer
from src.monkey_brain.kernel.pipeline.planning.trace import (
    PlanningTrace,
    build_planning_trace,
)


class IntegratedPlanningEngine:
    """PlanningEngine-conformant engine wiring Steps 8.1-8.6 together.

    Every collaborator is injectable and defaults to the Step 8.1-8.6
    building blocks; `fallback` defaults to a real LLMPlanner.
    `default_constraints` are applied to every generated candidate before
    validation (candidates from CandidateGenerator carry none of their own —
    Step 8.5 leaves that to the caller).
    """

    def __init__(
        self,
        decomposer: GoalDecomposer | None = None,
        generator: CandidateGenerator | None = None,
        constraint_engine: ConstraintEngine | None = None,
        scorer: PlanScorer | None = None,
        fallback: Any = None,
        default_constraints: tuple[PlanningConstraint, ...] = (),
    ) -> None:
        self._decomposer = decomposer or GoalDecomposer()
        self._generator = generator or CandidateGenerator()
        self._constraint_engine = constraint_engine or ConstraintEngine()
        self._scorer = scorer or PlanScorer()
        self._fallback = fallback or LLMPlanner()
        self._default_constraints = default_constraints

    def plan(self, belief: BeliefState, goal: Goal, context: Any = None) -> Plan:
        """The required PlanningEngine.plan() signature — unchanged."""
        return self.plan_with_trace(belief, goal, context)[0]

    def plan_with_trace(
        self,
        belief: BeliefState,
        goal: Goal,
        context: Any = None,
    ) -> tuple[Plan, PlanningTrace]:
        """Same as plan(), but also returns the PlanningTrace (Step 8.8) of
        how that decision was reached — for callers who want it directly
        rather than parsed back out of Plan.metadata["planning_trace"]."""
        domain_goal = self._to_domain_goal(goal)
        tree = self._decomposer.decompose_tree(domain_goal)

        candidates = self._generator.generate(domain_goal)
        if not candidates:
            trace = build_planning_trace(domain_goal, tree, candidates, None)
            return (
                self._fallback_plan(belief, goal, context, reason="no_registered_strategy", trace=trace),
                trace,
            )

        validated = self._validate_candidates(candidates)
        # Score once here so the trace reflects every candidate's actual
        # score, not just the winner's — select_best() alone would only
        # surface the winning candidate's score, leaving the rest looking
        # "unscored" in the trace even though they were, in fact, scored.
        scored = self._scorer.score_candidates(validated)
        best = self._scorer.select_best(scored)
        if best is None:
            trace = build_planning_trace(domain_goal, tree, scored, None)
            return (
                self._fallback_plan(belief, goal, context, reason="all_candidates_rejected", trace=trace),
                trace,
            )

        trace = build_planning_trace(domain_goal, tree, scored, best)
        return self._to_belief_plan(best, tree, trace), trace

    # ── Validate ─────────────────────────────────────────────────────────

    def _validate_candidates(self, candidates: tuple[PlanCandidate, ...]) -> tuple[PlanCandidate, ...]:
        validated: list[PlanCandidate] = []
        for candidate in candidates:
            plan_with_constraints = (
                replace(
                    candidate.plan,
                    constraints=candidate.plan.constraints + self._default_constraints,
                )
                if self._default_constraints
                else candidate.plan
            )
            validated_plan, report = self._constraint_engine.validated_plan(plan_with_constraints)
            validated.append(
                replace(
                    candidate,
                    plan=validated_plan,
                    rejected=not report.valid,
                    rejection_reason=("; ".join(report.violations) if not report.valid else ""),
                )
            )
        return tuple(validated)

    # ── Fallback ─────────────────────────────────────────────────────────

    def _fallback_plan(
        self,
        belief: BeliefState,
        goal: Goal,
        context: Any,
        *,
        reason: str,
        trace: PlanningTrace,
    ) -> Plan:
        # self._fallback defaults to a real LLMPlanner, whose .plan() is a
        # genuine async coroutine (see llm_planner.py's own docstring) —
        # this whole method stays sync (matching the PlanningEngine
        # Protocol/belief_runtime.py's inspect.iscoroutinefunction dispatch,
        # which already runs a sync engine's plan() via asyncio.to_thread,
        # i.e. always off the main thread's event loop, if any), so a
        # coroutine-returning fallback is run to completion here with
        # asyncio.run() rather than silently awaited-never (which returned
        # the coroutine object itself in place of a Plan).
        plan_result = self._fallback.plan(belief, goal, context)
        fallback_plan = asyncio.run(plan_result) if inspect.isawaitable(plan_result) else plan_result
        metadata = dict(fallback_plan.metadata)
        metadata["fallback_reason"] = reason
        metadata["planning_trace"] = trace.to_dict()
        return replace(fallback_plan, planner="integrated:fallback", metadata=metadata)

    # ── Conversion: belief_state.Goal -> planning.domain.Goal ──────────────

    def _to_domain_goal(self, goal: Goal) -> DomainGoal:
        return DomainGoal(
            name=goal.name,
            description=goal.description,
            completion_criteria=goal.success_criteria,
        )

    # ── Conversion: PlanCandidate -> belief_state.Plan ──────────────────────

    def _to_belief_plan(self, candidate: PlanCandidate, tree: GoalTree, trace: PlanningTrace) -> Plan:
        domain_plan = candidate.plan
        score = candidate.score if candidate.score is not None else 0.0

        steps = tuple(
            PlanStep(
                action=(
                    step.operator.name if step.operator is not None else (step.description or f"step_{step.sequence}")
                ),
                description=step.description,
                preconditions=step.preconditions,
                expected_outcome=step.expected_outcome,
                cost=step.estimated_cost,
                confidence=step.confidence,
            )
            for step in domain_plan.steps
        )
        preconditions = tuple(dict.fromkeys(p for s in domain_plan.steps for p in s.preconditions))
        outcomes = tuple(dict.fromkeys(s.expected_outcome for s in domain_plan.steps if s.expected_outcome))

        return Plan(
            goal=domain_plan.goal.name,
            preconditions=preconditions,
            steps=steps,
            expected_outcomes=outcomes,
            cost=domain_plan.estimated_cost,
            confidence=score,
            risk=round(1.0 - score, 4),
            goal_state=domain_plan.goal.name,
            planner="integrated",
            metadata={
                "strategy": domain_plan.metadata.get("strategy", ""),
                "validation_status": domain_plan.validation_status.value,
                "candidate_score": score,
                "subgoal_count": len(tree.children),
                "planning_trace": trace.to_dict(),
            },
        )
