"""Formal learning model and (eventually) learning algorithms — Step 10.

Step 10.1: domain.py — the immutable learning domain model.
Step 10.2: capture.py — ExperienceBuilder + serialization.
Step 10.3: reward.py — RewardEngine, the reward/evaluation algorithm.
Step 10.4: belief_learning.py — BeliefLearner, per-actor confidence updates.
Step 10.5: world_evolution.py — WorldEvolutionEngine, shared
relationship-strength updates.
Step 10.6: policies.py — LearningPolicyEngine, the behavioral
contract LearningPolicy (data) resolves to.
Step 10.7: integration.py — LearningIntegratedPolicy, wires the pipeline
into the live CognitiveRuntime's Learn stage.
Step 10.8: phi.py — PhiCompiler, the persistable Φ artifact; also wired
into integration.py's Compile-Φ stage override.
Step 10.9 (this): trace.py — LearningTrace, the full pipeline narrative
(Experience Capture -> Reward -> Belief -> World -> Policy -> Φ),
standalone/opt-in, mirroring Step 9.8's ExecutionTrace shape.
"""

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningEvent,
    LearningObservation,
    LearningSignal,
    LearningOutcome,
    Provenance,
    LearningExperience,
    LearningPolicy,
    LearningContext,
    LearningResult,
)
from src.monkey_brain.kernel.pipeline.learning.capture import (
    ExperienceBuilder,
    capture_experience,
    experience_to_dict,
)
from src.monkey_brain.kernel.pipeline.learning.reward import (
    RewardWeights,
    RewardBreakdown,
    ExperienceRewardEngine,
    compute_reward,
    evaluate_experience,
)
from src.monkey_brain.kernel.pipeline.learning.belief_learning import (
    BeliefUpdate,
    BeliefLearner,
    derive_belief_updates,
    apply_belief_learning,
)
from src.monkey_brain.kernel.pipeline.learning.world_evolution import (
    WorldRelationshipUpdate,
    WorldEvolutionEngine,
    derive_world_updates,
    apply_world_evolution,
)
from src.monkey_brain.kernel.pipeline.learning.policies import (
    LearningPolicyEngine,
    ReinforcementLearningPolicy,
    PassiveLearningPolicy,
    ConservativeLearningPolicy,
    resolve_policy,
    apply_learning_policy,
)
from src.monkey_brain.kernel.pipeline.learning.phi import (
    PhiArtifact,
    PhiCompiler,
    compile_phi,
    phi_to_dict,
)
from src.monkey_brain.kernel.pipeline.learning.integration import (
    LearningIntegratedPolicy,
    build_learning_integrated_runtime,
)
from src.monkey_brain.kernel.pipeline.learning.trace import (
    LearningTraceStage,
    LearningTrace,
    build_learning_trace,
    learn_with_trace,
)

__all__ = [
    "LearningEvent",
    "LearningObservation",
    "LearningSignal",
    "LearningOutcome",
    "Provenance",
    "LearningExperience",
    "LearningPolicy",
    "LearningContext",
    "LearningResult",
    "ExperienceBuilder",
    "capture_experience",
    "experience_to_dict",
    "RewardWeights",
    "RewardBreakdown",
    "ExperienceRewardEngine",
    "compute_reward",
    "evaluate_experience",
    "BeliefUpdate",
    "BeliefLearner",
    "derive_belief_updates",
    "apply_belief_learning",
    "WorldRelationshipUpdate",
    "WorldEvolutionEngine",
    "derive_world_updates",
    "apply_world_evolution",
    "LearningPolicyEngine",
    "ReinforcementLearningPolicy",
    "PassiveLearningPolicy",
    "ConservativeLearningPolicy",
    "resolve_policy",
    "apply_learning_policy",
    "PhiArtifact",
    "PhiCompiler",
    "compile_phi",
    "phi_to_dict",
    "LearningIntegratedPolicy",
    "build_learning_integrated_runtime",
    "LearningTraceStage",
    "LearningTrace",
    "build_learning_trace",
    "learn_with_trace",
]
