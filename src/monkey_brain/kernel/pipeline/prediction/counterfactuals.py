"""Counterfactual Reasoning (Step 11.4) — "What if?" branching scenarios.

Formally, a counterfactual is a *different transition model*: swap out
what's known to happen for one or more actions (e.g. "what if Store A is
closed?" changes what `drive_to_store_a` predicts), re-simulate the same
plan against that alternate knowledge, and compare against the baseline.
This reuses Step 11.2's TransitionPredictionEngine and Step 11.3's
SimulationEngine entirely through their public APIs -- no private-attribute
reaching, and each branch gets its own independent TransitionModel/
SimulationEngine pair built fresh, the same "each candidate gets its own
clone" isolation Step 11.3's simulate_candidates() already established.

CounterfactualAssumption/CounterfactualBranch live here rather than in
domain.py, matching the precedent every prior sub-step's first behavioral
module set (Step 11.2's WorldTransition/TransitionModel in transitions.py,
Step 11.3's SimulationState/SimulationTrajectory in simulation.py).

A real distinction this step surfaced: SimulationTrajectory.succeeded
(Step 11.3) measures confidence in a prediction, not goal achievement -- a
confidently predicted FAILURE (e.g. 98% probability the store is closed,
purchase fails) is still succeeded=True by that definition, since the
simulation knows what happens with high confidence; it's just bad news.
CounterfactualBranch therefore never describes a branch as "succeeding" or
"failing" -- judging whether a divergent outcome is good or bad for the
actor's goal is Step 11.5/11.6's job (risk/utility/scenario evaluation).
This step only reports what's directly true: which world facts diverge
from baseline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.pipeline.prediction.counterfactuals")

from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionModel,
    TransitionPredictionEngine,
    WorldTransition,
)
from src.monkey_brain.kernel.pipeline.prediction.simulation import (
    SimulationEngine,
    SimulationTrajectory,
)

UNSET = "<unset>"


@dataclass(frozen=True)
class CounterfactualAssumption:
    """One hypothetical override: for the named actions,
    substitute a different set of known transitions than the baseline
    model has registered. `category` groups assumptions the way Step
    11.4's own spec does: "availability", "inventory", "budget",
    "competition", or any other free-form label."""

    assumption_id: str = field(default_factory=lambda: uuid4().hex)
    description: str = ""
    category: str = ""
    transition_overrides: dict[str, tuple[WorldTransition, ...]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CounterfactualBranch:
    """One simulated "what if" branch, diverging from a baseline
    trajectory under one assumption."""

    branch_id: str = field(default_factory=lambda: uuid4().hex)
    assumption: CounterfactualAssumption = field(default_factory=CounterfactualAssumption)
    trajectory: SimulationTrajectory = field(default_factory=SimulationTrajectory)
    divergence: dict[str, Any] = field(default_factory=dict)
    """Per world-state key that differs between baseline and this branch's
    final state: {"baseline": ..., "counterfactual": ...}. A key missing
    from one side is represented as UNSET, not omitted."""
    impact_summary: str = ""
    error: str = ""
    """Empty (default) means this branch's simulation completed normally --
    same "" -means-absent convention as current_plan_store.py/transitions.py.
    Non-empty means simulate_plan() raised while producing this branch (see
    CounterfactualEngine.branch()); `trajectory`/`divergence`/`impact_summary`
    are then meaningless defaults, not real data, and callers (scenarios.py::
    scenarios_from_counterfactuals) must skip this branch rather than treat
    it as a real scenario -- a failed branch must never silently read as a
    successful (even a badly-scoring) one."""


class CounterfactualEngine:
    """Generates "what if" branches for a plan by substituting alternate
    transition knowledge and re-simulating, never mutating the baseline
    model or any shared state between branches."""

    def __init__(
        self,
        base_model: TransitionModel | None = None,
        uncertainty_threshold: float = 0.4,
    ) -> None:
        self._base_model = base_model or TransitionModel()
        self._uncertainty_threshold = uncertainty_threshold

    def simulate_baseline(self, world_snapshot: Any, belief_state: Any, plan: Any) -> SimulationTrajectory:
        return self._engine_for(self._base_model).simulate_plan(world_snapshot, belief_state, plan)

    def branch(
        self,
        world_snapshot: Any,
        belief_state: Any,
        plan: Any,
        assumption: CounterfactualAssumption,
        *,
        baseline: SimulationTrajectory | None = None,
    ) -> CounterfactualBranch:
        """Simulates one counterfactual branch. Accepts a precomputed
        baseline to avoid re-simulating it once per assumption when
        generating several branches at once.

        Never raises: a simulation failure for THIS assumption must not
        take down every other branch (or the baseline) being evaluated in
        the same prediction -- same non-fatal, logger.debug-then-continue
        convention used throughout this codebase (e.g. save_current_plan,
        _stream_event). Returns a CounterfactualBranch with `error` set
        instead; callers must treat that as "this scenario source did not
        produce a real result," never as a successful (even poorly-scoring)
        one -- see scenarios_from_counterfactuals()."""
        if baseline is None:
            baseline = self.simulate_baseline(world_snapshot, belief_state, plan)

        # transition_overrides is keyed by bare action name (no goal
        # concept at the assumption level -- see its own field docstring),
        # but known_transitions is keyed (goal_key, action_key) since the
        # Cross-Goal Plan Contamination fix. Scope each override to the
        # SAME goal_key simulate_plan() will look up below (same
        # canonicalize_goal(plan.goal) derivation as simulation.py::
        # simulate()) -- merging the bare string keys in directly would
        # make every override permanently unreachable (verified: a branch
        # would silently replay the baseline with an empty divergence
        # instead of applying the assumption).
        from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal

        # Widened (Goal-Key Contamination fix) to match simulation.py::
        # simulate()'s own widening -- plan.goal is standing-goal-name-
        # only, belief_state.goal.description carries the one-off
        # triggering text that actually distinguishes this request.
        plan_name = getattr(plan, "goal", "") or ""
        belief_goal = getattr(belief_state, "goal", None)
        description = getattr(belief_goal, "description", "") or ""
        goal_key = canonicalize_goal(f"{plan_name} {description}".strip())
        scoped_overrides = {
            (goal_key, action): transitions for action, transitions in assumption.transition_overrides.items()
        }
        counterfactual_model = TransitionModel(
            known_transitions={
                **self._base_model.known_transitions,
                **scoped_overrides,
            },
        )
        try:
            trajectory = self._engine_for(counterfactual_model).simulate_plan(world_snapshot, belief_state, plan)
        except Exception as exc:
            logger.debug(
                "CounterfactualEngine.branch: simulation failed for assumption %r (non-fatal): %s",
                assumption.description or assumption.assumption_id,
                exc,
                exc_info=True,
            )
            return CounterfactualBranch(assumption=assumption, error=str(exc) or exc.__class__.__name__)

        divergence = self._compute_divergence(baseline.final_state.world_state, trajectory.final_state.world_state)
        impact_summary = self._summarize_impact(assumption, divergence)

        return CounterfactualBranch(
            assumption=assumption,
            trajectory=trajectory,
            divergence=divergence,
            impact_summary=impact_summary,
        )

    def generate_branches(
        self,
        world_snapshot: Any,
        belief_state: Any,
        plan: Any,
        assumptions: tuple[CounterfactualAssumption, ...],
    ) -> tuple[CounterfactualBranch, ...]:
        """One branch per assumption, all diverging from the same baseline
        -- "support branching scenarios," Step 11.4's own spec."""
        baseline = self.simulate_baseline(world_snapshot, belief_state, plan)
        return tuple(self.branch(world_snapshot, belief_state, plan, a, baseline=baseline) for a in assumptions)

    # ── Internals ────────────────────────────────────────────────────────

    def _engine_for(self, model: TransitionModel) -> SimulationEngine:
        return SimulationEngine(TransitionPredictionEngine(model, self._uncertainty_threshold))

    def _compute_divergence(
        self, baseline_state: dict[str, Any], counterfactual_state: dict[str, Any]
    ) -> dict[str, Any]:
        keys = set(baseline_state) | set(counterfactual_state)
        diff: dict[str, Any] = {}
        for key in keys:
            b = baseline_state.get(key, UNSET)
            c = counterfactual_state.get(key, UNSET)
            if b != c:
                diff[key] = {"baseline": b, "counterfactual": c}
        return diff

    def _summarize_impact(self, assumption: CounterfactualAssumption, divergence: dict[str, Any]) -> str:
        """Deliberately does NOT describe this branch as "succeeding" or
        "failing" -- SimulationTrajectory.succeeded (Step 11.3) measures
        confidence in a prediction, not goal achievement (a confidently
        predicted failure is still succeeded=True by that definition, a
        distinction this step's own tests surfaced). Judging whether a
        divergent outcome is good or bad for the actor's goal is Step
        11.5/11.6's job (risk/utility/scenario evaluation), not this
        one's -- so the impact summary reports only what's directly true
        here: which world facts diverge from baseline."""
        if not divergence:
            return f"{assumption.description}: no change to predicted outcome"
        changed_keys = ", ".join(sorted(divergence.keys()))
        return f"{assumption.description}: {len(divergence)} world fact(s) diverge ({changed_keys})"
