"""Simulation Engine (Step 11.3) — simulates candidate plans before
execution, one step at a time:

    Current State -> Apply Step 1 -> Apply Step 2 -> ... -> Predicted State

Builds directly on Step 11.2's TransitionPredictionEngine (single-action
transition prediction) the same way Step 9.3's ExecutionScheduler built on
Step 9.2's ExecutionRegistry/handlers: this step owns *sequencing* a whole
plan's steps and tracking the resulting trajectory, not re-deriving
per-step transition prediction from scratch.

"Simulation must not modify the real world. It operates entirely on
cloned semantic state" (Step 11.3's own spec) is enforced two ways:
    1. TransitionPredictionEngine is already read-only (Step 11.2,
       verified: it never mutates world_snapshot/belief_state, only reads
       via getattr).
    2. clone_state() explicitly deep-copies world_snapshot/belief_state
       ONCE before simulation begins, and SimulationTrajectory stores the
       clones (not the caller's original references) -- so even a future
       TransitionModel implementation that isn't as careful still can't
       leak a mutation back to the caller's real objects. Verified
       directly by identity checks (`is not`), not just equality.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionKind,
    TransitionPredictionEngine,
    WorldTransition,
)

SUCCESS_PROBABILITY_THRESHOLD = 0.5


def clone_state(value: Any) -> Any:
    """Deep-copies arbitrary semantic state so simulation never touches
    the caller's original object -- the explicit "State cloning"
    deliverable. Falls back to returning the value itself only if it is
    not copyable (e.g. a live Mock/thread lock some test doubles carry) --
    still never mutated by this module either way, since nothing here
    calls a method on it, only getattr reads happen downstream in
    TransitionPredictionEngine."""
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


@dataclass(frozen=True)
class SimulationState:
    """One point along a simulated plan's trajectory."""

    step_index: int = -1
    """-1 for the initial state, before any step has been applied."""
    step_description: str = "initial"
    world_state: dict[str, Any] = field(default_factory=dict)
    """Accumulated predicted world deltas up to and including this step."""
    applied_transition: WorldTransition | None = None
    depends_on: tuple[int, ...] = ()
    """Mirrors the originating PlanStep.depends_on (belief_state.py) --
    0-based indices, WITHIN THIS SAME TRAJECTORY'S states, of steps this
    one requires to have succeeded first. Empty (the default, and the
    initial state's permanent value) means no explicit dependency was
    declared. Carried onto SimulationState (not just left on the PlanStep)
    so kernel/pipeline/prediction/risk.py::RiskEngine can walk the
    trajectory alone, without needing the original Plan object back."""


@dataclass(frozen=True)
class SimulationTrajectory:
    """The full step-by-step path from Current State to Predicted State
    for one candidate plan."""

    trajectory_id: str = field(default_factory=lambda: uuid4().hex)
    plan: Any = None
    initial_world_snapshot: Any = None
    """The CLONE simulation actually ran against -- never the caller's
    original world_snapshot reference."""
    belief_snapshot: Any = None
    """The CLONE simulation actually ran against -- never the caller's
    original belief_state reference."""
    states: tuple[SimulationState, ...] = ()
    """states[0] is always the initial state; states[i] is the state
    after applying plan step i-1."""
    final_state: SimulationState = field(default_factory=SimulationState)
    succeeded: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class SimulationEngine:
    """Simulates candidate plans step-by-step against cloned semantic
    state, using a TransitionPredictionEngine to predict each step's
    effect."""

    def __init__(self, transition_engine: TransitionPredictionEngine | None = None) -> None:
        self._transition_engine = transition_engine or TransitionPredictionEngine()

    def simulate(
        self,
        world_snapshot: Any,
        belief_state: Any,
        steps: tuple[Any, ...],
        *,
        plan: Any = None,
    ) -> SimulationTrajectory:
        """Simulates a raw sequence of steps (each duck-typed the same way
        TransitionPredictionEngine treats an action: a `.description` or
        `.name`, or anything str()-able).

        goal_key (Cross-Goal Plan Contamination fix) is derived from
        `plan.goal` when a real plan is passed (the normal case -- every
        real caller in this pipeline threads `plan` through), so each
        step's transition lookup is scoped to the plan's own goal and
        can't be poisoned by an unrelated goal's learned failure history
        for the same action name."""
        from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal

        # Widened (Goal-Key Contamination fix): plan.goal only ever
        # carries the standing goal NAME (llm_planner.py's Plan
        # construction never sets it to anything else), never the
        # one-off triggering description that actually distinguishes
        # one request from another sharing the same standing goal.
        # belief_state.goal.description (when belief_state is a real
        # BeliefState) carries that -- folded in here to match the
        # SAME widening applied to every other read/write site of this
        # key (belief_runtime.py's _predict/_run_decide, comparison/
        # integration.py's Learn stage, counterfactuals.py::branch()),
        # so a learned transition is never written under a narrower key
        # than what a later, unrelated request looks up.
        plan_name = getattr(plan, "goal", "") or ""
        belief_goal = getattr(belief_state, "goal", None)
        description = getattr(belief_goal, "description", "") or ""
        goal_key = canonicalize_goal(f"{plan_name} {description}".strip()) if plan is not None else ""

        cloned_world = clone_state(world_snapshot)
        cloned_belief = clone_state(belief_state)

        initial_state = SimulationState(step_index=-1, step_description="initial", world_state={})
        states: list[SimulationState] = [initial_state]

        accumulated: dict[str, Any] = {}
        for i, step in enumerate(steps):
            transitions = self._transition_engine.predict_transitions(cloned_world, cloned_belief, step, goal_key)
            chosen = self._transition_engine.most_likely(transitions)
            accumulated = {**accumulated, **chosen.resulting_world_delta}
            states.append(
                SimulationState(
                    step_index=i,
                    step_description=self._step_description(step),
                    world_state=dict(accumulated),
                    applied_transition=chosen,
                    depends_on=tuple(getattr(step, "depends_on", ()) or ()),
                )
            )

        final_state = states[-1]
        succeeded = self._succeeded(states)

        return SimulationTrajectory(
            plan=plan,
            initial_world_snapshot=cloned_world,
            belief_snapshot=cloned_belief,
            states=tuple(states),
            final_state=final_state,
            succeeded=succeeded,
        )

    def simulate_plan(self, world_snapshot: Any, belief_state: Any, plan: Any) -> SimulationTrajectory:
        """Simulates a candidate plan object -- duck-typed via `.steps`
        (matching belief_state.Plan / planning.domain.Plan /
        execution_runtime.domain.ExecutionPlan's shared shape, without
        importing any of them), or treats `plan` itself as the step
        sequence if it's already a bare list/tuple."""
        steps = self._extract_steps(plan)
        return self.simulate(world_snapshot, belief_state, steps, plan=plan)

    def simulate_candidates(
        self, world_snapshot: Any, belief_state: Any, plans: tuple[Any, ...]
    ) -> tuple[SimulationTrajectory, ...]:
        """Simulates every candidate plan independently -- each gets its
        own cloned state, so simulating one candidate can never influence
        another's trajectory. Step 11.6 (Multi-Scenario Evaluation) is
        what ranks the resulting trajectories; this just produces them."""
        return tuple(self.simulate_plan(world_snapshot, belief_state, plan) for plan in plans)

    # ── Internals ────────────────────────────────────────────────────────

    def _extract_steps(self, plan: Any) -> tuple[Any, ...]:
        steps = getattr(plan, "steps", None)
        if steps is not None:
            return tuple(steps)
        if isinstance(plan, (list, tuple)):
            return tuple(plan)
        return ()

    def _step_description(self, step: Any) -> str:
        return str(getattr(step, "description", None) or getattr(step, "name", None) or step)

    def _succeeded(self, states: tuple[SimulationState, ...]) -> bool:
        applied = [s.applied_transition for s in states if s.applied_transition is not None]
        if not applied:
            return True
        return all(t.kind != TransitionKind.UNKNOWN and t.probability >= SUCCESS_PROBABILITY_THRESHOLD for t in applied)
