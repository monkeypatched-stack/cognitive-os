"""Runtime Integration (Step 11.7) — wires Steps 11.2-11.6's prediction
pipeline into the LIVE CognitiveRuntime's Predict stage:

    Plan -> Simulation -> Prediction -> Risk Analysis -> Scenario Evaluation
    -> Prediction Result

with zero modifications to RequestCompiler, PipelineOrchestrator,
PlanningEngine, ExecutionEngine, or LearningEngine.

Extension point and technique
-------------------------------
Identical to Step 10.7's LearningIntegratedPolicy: CognitivePolicy's own
docstring names subclass-and-override-configure() as the sanctioned
customization surface, and configure() is called FROM WITHIN
CognitiveRuntime.__init__ with `predict=self._predict` (the real runtime
instance's own bound method), so the override receives a correctly-bound
reference with no double-construction.

PredictionIntegratedPolicy *extends* LearningIntegratedPolicy rather than
CognitivePolicy directly -- this reuses Step 10.7/10.8's already-tested
Learn/Compile-Φ overrides via ordinary inheritance (super().configure()),
zero duplication, and zero modification of learning/integration.py itself
(satisfying "no changes to LearningEngine" while still composing with it,
which is a different thing than modifying it). The result: all three
stages the acceptance criteria's full lifecycle diagram shows enhanced
(Learn, Compile Φ, Predict) come from one policy object.

Why the original _predict still runs first
---------------------------------------------
_predict's current body is not a no-op like _compile_phi's was (Step
11.7's own _compile_phi precedent) -- it calls
`belief.record_prediction(...)`, and _commit (untouched, no part of Step
11) reads `belief.predictions` directly (`predictions=tuple({...} for p in
belief.predictions)`). Confirmed by reading _commit directly, not
assumed. So integrated_predict runs the original callback first,
preserving that side effect exactly, then layers the new pipeline's
output onto a NEW attribute (`state.prediction_result`) rather than
touching anything _commit already depends on.

Honesty about the transition model
-------------------------------------
"No changes to ... PlanningEngine, ExecutionEngine" rules out reverse-
engineering real transition knowledge from those engines' internals, and
there's no live semantic world model wired in yet -- so this integration
accepts a caller-supplied TransitionModel (default: empty). Absent real
knowledge, every action predicts as UNKNOWN (Step 11.2's explicit
"missing knowledge" case), which is honest, not silently optimistic.
Callers with real knowledge (or a future Step 11.10 policy) supply a
populated TransitionModel for richer predictions.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from src.monkey_brain.kernel.pipeline.cognitive_policy import StageFn
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.learning.domain import LearningPolicy
from src.monkey_brain.kernel.pipeline.learning.integration import (
    LearningIntegratedPolicy,
)

from src.monkey_brain.kernel.pipeline.prediction.transitions import TransitionModel
from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import (
    CounterfactualAssumption,
)
from src.monkey_brain.kernel.pipeline.prediction.policies import (
    DeterministicPredictionPolicy,
    PredictionPolicyInput,
)
from src.monkey_brain.kernel.pipeline.prediction.domain import PredictionResult
from src.monkey_brain.kernel.pipeline.learning.domain import Provenance

# Real gap this closed: this used to be its own independent module-level
# `DEFAULT_REJECTION_THRESHOLD = 0.3` -- same name, different constant,
# never imported from scenarios.py's real definition. Confirmed live: a
# scenarios.py-side threshold change (0.3 -> 0.15, made to stop
# structurally-penalizing every multi-step plan for having multiple
# steps) silently did nothing for any caller that relied on THIS file's
# own default instead of passing an explicit override -- exactly the
# grocery vertical's real request pipeline (comparison/integration.py::
# build_comparison_integrated_runtime), whose own rejection_threshold
# default was ALSO an independent, unsynced 0.3 literal until now.
from src.monkey_brain.kernel.pipeline.prediction.scenarios import (
    DEFAULT_REJECTION_THRESHOLD,
)


def _safe_serialize(value: Any) -> Any:
    """Same graceful-degradation convention learning/capture.py's
    experience_to_dict() established: dataclasses -> asdict() recursively,
    dicts/lists/tuples recurse, enums via .value, everything else -> str()."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "value") and type(value).__name__.endswith("Kind"):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _safe_serialize(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {k: _safe_serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_serialize(v) for v in value]
    return str(value)


def prediction_result_to_dict(result: PredictionResult) -> dict[str, Any]:
    return _safe_serialize(result)


class PredictionIntegratedPolicy(LearningIntegratedPolicy):
    """Standard 9-stage lifecycle with Learn/Compile-Φ enhanced (inherited
    from Step 10.7/10.8) and Predict enhanced with the Step 11 prediction
    pipeline."""

    def __init__(
        self,
        learning_policy: LearningPolicy = LearningPolicy(),
        transition_model: TransitionModel | None = None,
        counterfactual_assumptions: tuple[CounterfactualAssumption, ...] = (),
        rejection_threshold: float = DEFAULT_REJECTION_THRESHOLD,
        time_horizon: float = 0.0,
    ) -> None:
        super().__init__(learning_policy=learning_policy)
        self._transition_model = transition_model or TransitionModel()
        self._counterfactual_assumptions = counterfactual_assumptions
        self._rejection_threshold = rejection_threshold
        self._time_horizon = time_horizon

    def configure(
        self,
        observe: StageFn,
        believe: StageFn,
        plan: StageFn,
        execute: StageFn,
        observe_outcome: StageFn,
        learn: StageFn,
        compile_phi: StageFn,
        predict: StageFn,
        commit: StageFn,
    ) -> None:
        original_predict = predict

        async def integrated_predict(state: CognitiveState) -> CognitiveState:
            state = await original_predict(state)
            import time as _time

            _predict_start = _time.time()

            # Delegates to the real DeterministicPredictionPolicy
            # (policies.py) instead of inlining the CounterfactualEngine/
            # ScenarioEvaluator sequence directly -- the two were a
            # near-duplicate (same real classes, same call order) before
            # this change, with the PredictionPolicy Protocol/
            # PredictionPolicyRegistry left as unreachable scaffolding.
            # Delegating makes that seam genuinely exercised by the one
            # real live call site; behavior (the resulting
            # state.prediction_result dict) is unchanged, verified
            # functionally, not just asserted.
            #
            # One id shared by every candidate this call produces (and by
            # the PredictionResult itself), and real attribution instead
            # of an always-empty Provenance() -- both were previously-
            # declared-but-never-populated fields (Cross-Solver
            # Participation hardening pass).
            prediction_id = uuid.uuid4().hex
            provenance = Provenance(
                actor_id=state.actor_id,
                run_id=state.metrics.get("execution_id", ""),
                source="prediction_integrated_policy",
            )
            policy = DeterministicPredictionPolicy(
                transition_model=self._transition_model,
                counterfactual_assumptions=self._counterfactual_assumptions,
                rejection_threshold=self._rejection_threshold,
            )
            policy_result = policy.predict(
                PredictionPolicyInput(
                    world_snapshot=state.world_snapshot,
                    belief_state=state.belief,
                    plan=state.belief.plan,
                    time_horizon=self._time_horizon,
                    provenance=provenance,
                    prediction_id=prediction_id,
                )
            )

            state.prediction_result = prediction_result_to_dict(policy_result.result)

            # Lemon metrics (previously zero telemetry anywhere on this
            # stage — confirmed by grepping every _obs call site in the
            # kernel). Real values only: candidate count and selected
            # probability come straight off the PredictionResult this call
            # just produced, never fabricated.
            from src.monkey_brain.kernel.compile import _obs

            result = policy_result.result
            selected = result.selected
            _obs.counter("prediction.total", recommendation=result.recommendation or "unknown")
            _obs.gauge("prediction.candidates", float(len(result.candidates)))
            if selected is not None:
                _obs.gauge("prediction.selected_probability", float(selected.probability))
            _obs.histogram("prediction.duration_ms", (_time.time() - _predict_start) * 1000.0)
            return state

        # Delegates to LearningIntegratedPolicy.configure(), which wraps
        # learn/compile_phi with Step 10.7/10.8's own overrides and then
        # calls CognitivePolicy.configure() with the final 9-stage list --
        # zero duplication of that logic here.
        super().configure(
            observe,
            believe,
            plan,
            execute,
            observe_outcome,
            learn,
            compile_phi,
            integrated_predict,
            commit,
        )


def build_prediction_integrated_runtime(
    *,
    learning_policy: LearningPolicy = LearningPolicy(),
    transition_model: TransitionModel | None = None,
    counterfactual_assumptions: tuple[CounterfactualAssumption, ...] = (),
    rejection_threshold: float = DEFAULT_REJECTION_THRESHOLD,
    time_horizon: float = 0.0,
    observation_provider: Any = None,
    belief_fusion: Any = None,
    planning_engine: Any = None,
    plan_validator: Any = None,
    execution_engine: Any = None,
    event_bus: Any = None,
):
    """Convenience factory: a CognitiveRuntime with Learn, Compile-Φ, and
    Predict all enhanced via PredictionIntegratedPolicy, composing freely
    with Steps 8.7/9.7's IntegratedPlanningEngine/IntegratedExecutionEngine.
    Import of CognitiveRuntime is deferred to call time, the same
    lazy-import shape Step 10.7 used for the identical reason."""
    from src.monkey_brain.kernel.pipeline.belief_runtime import (
        CognitiveRuntime as PipelineCognitiveRuntime,
    )

    return PipelineCognitiveRuntime(
        observation_provider=observation_provider,
        belief_fusion=belief_fusion,
        planning_engine=planning_engine,
        plan_validator=plan_validator,
        execution_engine=execution_engine,
        policy=PredictionIntegratedPolicy(
            learning_policy=learning_policy,
            transition_model=transition_model,
            counterfactual_assumptions=counterfactual_assumptions,
            rejection_threshold=rejection_threshold,
            time_horizon=time_horizon,
        ),
        event_bus=event_bus,
    )
