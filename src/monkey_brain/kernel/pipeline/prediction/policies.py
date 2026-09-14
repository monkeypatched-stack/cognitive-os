"""Predictive Policy Framework (Step 11.10) — makes prediction strategies
pluggable behind a common interface.

The runtime should depend only on:

    PredictionPolicy.predict(...)

Supporting multiple prediction strategies behind one interface:
    - deterministic world-model prediction (default: the existing
      counterfactual + scenario pipeline)
    - probabilistic prediction
    - Monte Carlo simulation (future)
    - graph search prediction (future)
    - Bayesian prediction (future)
    - LLM-assisted prediction (future)

Follows the same Protocol-based extension pattern as:
    - PlanningEngine Protocol (planner.py) with LLMPlanner
      (llm_planner.py)
    - ExecutionEngine Protocol (execution.py) with ActionExecutor
      (action_executor.py)

The existing CounterfactualEngine -> ScenarioEvaluator pipeline becomes
DeterministicPredictionPolicy, the default implementation. Future policies
implement PredictionPolicy directly and compose with PredictionIntegratedPolicy
via the same constructor injection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from src.monkey_brain.kernel.pipeline.prediction.domain import PredictionResult
from src.monkey_brain.kernel.pipeline.prediction.transitions import TransitionModel
from src.monkey_brain.kernel.pipeline.prediction.simulation import SimulationTrajectory
from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import (
    CounterfactualAssumption,
    CounterfactualEngine,
)
from src.monkey_brain.kernel.pipeline.prediction.scenarios import (
    ScenarioEvaluator,
    scenarios_from_counterfactuals,
    build_scenario_participation,
    DEFAULT_REJECTION_THRESHOLD,
)


@dataclass(frozen=True)
class PredictionPolicyInput:
    """What every prediction policy receives: the current world snapshot,
    belief state, plan, and configuration. Kept minimal -- policies that
    need richer context (e.g. Monte Carlo needs a budget) extend via
    metadata."""

    world_snapshot: Any = None
    belief_state: Any = None
    plan: Any = None
    time_horizon: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: Any = None
    """Optional kernel/pipeline/learning/domain.py::Provenance -- who/when
    this prediction is really for. None (default) preserves every
    existing caller's exact prior behavior (each Prediction gets its own
    unattributed default Provenance). Any-typed rather than importing
    Provenance here to keep this dataclass's own import surface minimal,
    matching this module's existing convention (world_snapshot/belief_state
    /plan are already Any for the same reason)."""
    prediction_id: str | None = None
    """Optional shared id for every candidate this input produces. None
    (default) preserves prior behavior -- each Prediction/PredictionResult
    mints/leaves its own independent id."""


@dataclass(frozen=True)
class PredictionPolicyResult:
    """What every prediction policy produces: a PredictionResult plus
    optional provenance metadata. Policies that produce richer output
    (e.g. trajectory sets for Monte Carlo) attach them via metadata."""

    result: PredictionResult = field(default_factory=PredictionResult)
    trajectories: tuple[SimulationTrajectory, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PredictionPolicy(Protocol):
    """The pluggable interface the runtime depends on. Every prediction
    strategy implements this single method.

    predict() must be:
        - deterministic for the same inputs (when the policy itself is
          deterministic -- probabilistic policies document their own
          reproducibility contract)
        - side-effect-free (never mutates world_snapshot/belief_state/plan)
        - independent (no shared mutable state between calls)
    """

    def predict(self, input: PredictionPolicyInput) -> PredictionPolicyResult:
        """Predict the outcome of executing a plan from the given world
        state and belief state, returning a ranked PredictionResult."""
        ...


class DeterministicPredictionPolicy:
    """The default prediction policy: the existing counterfactual + scenario
    pipeline (Steps 11.2-11.6), wrapped behind the PredictionPolicy interface.

    This is the "deterministic world-model prediction" from Step 11.10's
    spec: given a TransitionModel, CounterfactualAssumptions, and a
    rejection threshold, it deterministically produces the same
    PredictionResult for the same inputs.

    Future policies (Monte Carlo, Bayesian, LLM-assisted) implement
    PredictionPolicy.predict() directly and supply different trajectory
    generation strategies, but produce the same PredictionPolicyResult
    shape for PredictionIntegratedPolicy to consume.
    """

    def __init__(
        self,
        transition_model: TransitionModel | None = None,
        counterfactual_assumptions: tuple[CounterfactualAssumption, ...] = (),
        rejection_threshold: float = DEFAULT_REJECTION_THRESHOLD,
    ) -> None:
        self._transition_model = transition_model or TransitionModel()
        self._counterfactual_assumptions = counterfactual_assumptions
        self._rejection_threshold = rejection_threshold

    def predict(self, input: PredictionPolicyInput) -> PredictionPolicyResult:
        cf_engine = CounterfactualEngine(self._transition_model)
        baseline = cf_engine.simulate_baseline(input.world_snapshot, input.belief_state, input.plan)
        branches = tuple(
            cf_engine.branch(
                input.world_snapshot,
                input.belief_state,
                input.plan,
                assumption,
                baseline=baseline,
            )
            for assumption in self._counterfactual_assumptions
        )
        scenarios = scenarios_from_counterfactuals("Baseline", baseline, branches)

        assumption_labels = tuple(
            a.description or a.category or "Unnamed scenario" for a in self._counterfactual_assumptions
        )
        participation = build_scenario_participation("Baseline", assumption_labels, branches)

        evaluator = ScenarioEvaluator(rejection_threshold=self._rejection_threshold)
        result = evaluator.evaluate_and_recommend(
            scenarios,
            time_horizon=input.time_horizon,
            provenance=input.provenance,
            prediction_id=input.prediction_id,
            participation=participation,
        )

        trajectories = (baseline,)
        trajectories = trajectories + tuple(b.trajectory for b in branches)

        return PredictionPolicyResult(
            result=result,
            trajectories=trajectories,
            metadata={
                "policy": "deterministic",
                "branch_count": len(branches),
                "scenario_count": len(scenarios),
            },
        )


class PredictionPolicyRegistry:
    """A simple registry for named prediction policies, allowing runtime
    selection by name. Not required -- callers can inject a policy
    instance directly -- but convenient for configuration-driven setups."""

    def __init__(self) -> None:
        self._policies: dict[str, PredictionPolicy] = {}

    def register(self, name: str, policy: PredictionPolicy) -> None:
        self._policies[name] = policy

    def get(self, name: str) -> PredictionPolicy:
        if name not in self._policies:
            available = ", ".join(sorted(self._policies)) or "(none)"
            raise KeyError(f"Unknown prediction policy '{name}'. Available: {available}")
        return self._policies[name]

    def list_policies(self) -> tuple[str, ...]:
        return tuple(sorted(self._policies.keys()))

    @classmethod
    def with_defaults(cls) -> PolicyRegistry:
        """A registry pre-loaded with the default deterministic policy."""
        registry = cls()
        registry.register("deterministic", DeterministicPredictionPolicy())
        return registry
