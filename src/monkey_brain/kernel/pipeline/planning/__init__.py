"""Formal planning model and (eventually) planning algorithms — Step 8.

Step 8.1: domain.py — the immutable planning domain model.
Step 8.2: operators.py — the operator interface + sample operator library.
Step 8.3: decomposition.py — hierarchical goal decomposition.
Step 8.4: constraints.py — the constraint evaluator + validation report.
Step 8.5: candidates.py — alternative plan (candidate) generation.
Step 8.6: scoring.py — pluggable candidate scoring + selection.
Step 8.7: integration.py — IntegratedPlanningEngine, a drop-in PlanningEngine
(planner.py Protocol) wiring 8.1-8.6 together, injectable via
CognitiveRuntime(planning_engine=...) with zero changes to any existing file.
Step 8.8 (this): trace.py — PlanningTrace + explainability, attached to
every Plan's metadata["planning_trace"] by IntegratedPlanningEngine.
"""

from src.monkey_brain.kernel.pipeline.planning.domain import (
    ValidationStatus,
    Goal,
    SubGoal,
    PlanningOperator,
    PlanningConstraint,
    PlanStep,
    Plan,
    PlanCandidate,
    PlanningContext,
)
from src.monkey_brain.kernel.pipeline.planning.operators import (
    OperatorTemplate,
    AcquireItem,
    Navigate,
    QueryInventory,
    Wait,
    Notify,
    ReserveResource,
    OPERATOR_TEMPLATES,
)
from src.monkey_brain.kernel.pipeline.planning.decomposition import (
    SubGoalSpec,
    DecompositionRule,
    GoalTree,
    GoalDecomposer,
    DEFAULT_RULES,
)
from src.monkey_brain.kernel.pipeline.planning.constraints import (
    ConstraintCheckResult,
    ValidationReport,
    ConstraintEvaluator,
    BudgetConstraintEvaluator,
    TimeConstraintEvaluator,
    PolicyConstraintEvaluator,
    CapabilityConstraintEvaluator,
    InventoryConstraintEvaluator,
    SafetyConstraintEvaluator,
    PermissionsConstraintEvaluator,
    DEFAULT_EVALUATORS,
    ConstraintEngine,
)
from src.monkey_brain.kernel.pipeline.planning.candidates import (
    OperatorCall,
    PlanStrategy,
    GenerationRule,
    DEFAULT_GENERATION_RULES,
    CandidateGenerator,
)
from src.monkey_brain.kernel.pipeline.planning.scoring import (
    ScoreBreakdown,
    ScoringPolicy,
    DefaultScoringPolicy,
    PlanScorer,
)
from src.monkey_brain.kernel.pipeline.planning.integration import (
    IntegratedPlanningEngine,
)
from src.monkey_brain.kernel.pipeline.planning.trace import (
    PlanningTrace,
    build_planning_trace,
)

__all__ = [
    "ValidationStatus",
    "Goal",
    "SubGoal",
    "PlanningOperator",
    "PlanningConstraint",
    "PlanStep",
    "Plan",
    "PlanCandidate",
    "PlanningContext",
    "OperatorTemplate",
    "AcquireItem",
    "Navigate",
    "QueryInventory",
    "Wait",
    "Notify",
    "ReserveResource",
    "OPERATOR_TEMPLATES",
    "SubGoalSpec",
    "DecompositionRule",
    "GoalTree",
    "GoalDecomposer",
    "DEFAULT_RULES",
    "ConstraintCheckResult",
    "ValidationReport",
    "ConstraintEvaluator",
    "BudgetConstraintEvaluator",
    "TimeConstraintEvaluator",
    "PolicyConstraintEvaluator",
    "CapabilityConstraintEvaluator",
    "InventoryConstraintEvaluator",
    "SafetyConstraintEvaluator",
    "PermissionsConstraintEvaluator",
    "DEFAULT_EVALUATORS",
    "ConstraintEngine",
    "OperatorCall",
    "PlanStrategy",
    "GenerationRule",
    "DEFAULT_GENERATION_RULES",
    "CandidateGenerator",
    "ScoreBreakdown",
    "ScoringPolicy",
    "DefaultScoringPolicy",
    "PlanScorer",
    "IntegratedPlanningEngine",
    "PlanningTrace",
    "build_planning_trace",
]
