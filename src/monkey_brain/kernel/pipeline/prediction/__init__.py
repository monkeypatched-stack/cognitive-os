"""Formal prediction model and prediction algorithms — Step 11.

Step 11.1: domain.py — the immutable prediction domain model.
Step 11.2: transitions.py — TransitionPredictionEngine, world-state
transition prediction.
Step 11.3: simulation.py — SimulationEngine, step-by-step plan simulation
over cloned semantic state.
Step 11.4: counterfactuals.py — CounterfactualEngine, "what if?" branch
generation.
Step 11.5: risk.py — RiskEngine, probability of success / expected reward
/ execution risk / confidence intervals.
Step 11.6: scenarios.py — ScenarioEvaluator, multi-scenario ranking and
recommendation.
Step 11.7: integration.py — PredictionIntegratedPolicy, wires the
pipeline into the live CognitiveRuntime's Predict stage.
Step 11.8: compiler.py — PredictionCompiler, deterministic compilation of
prediction outputs into reusable artifacts.
Step 11.9: trace.py — PredictionTrace, structured explainability for
prediction cycles.
Step 11.10: policies.py — PredictionPolicy interface, pluggable
prediction strategies with default deterministic implementation.
"""

from src.monkey_brain.kernel.pipeline.prediction.domain import (
    PredictionConfidence,
    PredictionOutcome,
    Prediction,
    PredictionRequest,
    PredictionContext,
    PredictionCandidate,
    PredictionTrace,
    PredictionResult,
)
from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionKind,
    WorldTransition,
    TransitionModel,
    TransitionPredictionEngine,
)
from src.monkey_brain.kernel.pipeline.prediction.simulation import (
    SimulationState,
    SimulationTrajectory,
    SimulationEngine,
    clone_state,
)
from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import (
    CounterfactualAssumption,
    CounterfactualBranch,
    CounterfactualEngine,
)
from src.monkey_brain.kernel.pipeline.prediction.risk import (
    RiskFactor,
    RiskAssessment,
    RiskEngine,
    enrich_prediction,
)
from src.monkey_brain.kernel.pipeline.prediction.scenarios import (
    ScenarioInput,
    ScenarioEvaluator,
    scenarios_from_counterfactuals,
)
from src.monkey_brain.kernel.pipeline.prediction.integration import (
    PredictionIntegratedPolicy,
    build_prediction_integrated_runtime,
    prediction_result_to_dict,
)
from src.monkey_brain.kernel.pipeline.prediction.compiler import (
    PredictionCompiler,
    PredictionSummary,
    ScenarioSet,
    ScenarioSummary,
    ConfidenceReport,
    DecisionRecommendation,
    prediction_summary_to_dict,
    compile_prediction,
)
from src.monkey_brain.kernel.pipeline.prediction.trace import (
    PredictionTrace as PredictionTraceRecord,
    ScenarioTraceEntry,
    build_prediction_trace,
    predict_with_trace,
)
from src.monkey_brain.kernel.pipeline.prediction.policies import (
    PredictionPolicy,
    PredictionPolicyInput,
    PredictionPolicyResult,
    DeterministicPredictionPolicy,
    PredictionPolicyRegistry,
)

__all__ = [
    # Domain (11.1)
    "PredictionConfidence",
    "PredictionOutcome",
    "Prediction",
    "PredictionRequest",
    "PredictionContext",
    "PredictionCandidate",
    "PredictionTrace",
    "PredictionResult",
    # Transitions (11.2)
    "TransitionKind",
    "WorldTransition",
    "TransitionModel",
    "TransitionPredictionEngine",
    # Simulation (11.3)
    "SimulationState",
    "SimulationTrajectory",
    "SimulationEngine",
    "clone_state",
    # Counterfactuals (11.4)
    "CounterfactualAssumption",
    "CounterfactualBranch",
    "CounterfactualEngine",
    # Risk (11.5)
    "RiskFactor",
    "RiskAssessment",
    "RiskEngine",
    "enrich_prediction",
    # Scenarios (11.6)
    "ScenarioInput",
    "ScenarioEvaluator",
    "scenarios_from_counterfactuals",
    # Integration (11.7)
    "PredictionIntegratedPolicy",
    "build_prediction_integrated_runtime",
    "prediction_result_to_dict",
    # Compiler (11.8)
    "PredictionCompiler",
    "PredictionSummary",
    "ScenarioSet",
    "ScenarioSummary",
    "ConfidenceReport",
    "DecisionRecommendation",
    "prediction_summary_to_dict",
    "compile_prediction",
    # Trace (11.9)
    "PredictionTraceRecord",
    "ScenarioTraceEntry",
    "build_prediction_trace",
    "predict_with_trace",
    # Policies (11.10)
    "PredictionPolicy",
    "PredictionPolicyInput",
    "PredictionPolicyResult",
    "DeterministicPredictionPolicy",
    "PolicyRegistry",
]
