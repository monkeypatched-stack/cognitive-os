"""MonkeyBrain Cognitive Kernel (Deadpool).

The Kernel owns cognition only.

Responsibilities:
- Intent: classify what the user wants
- Goal: structure the intent for execution
- Planner: generate candidate capability pipelines
- Policy: select which pipeline to execute (Bellman/RL)
- LLM Explorer: generate candidate workflows (exploration)
- ExecutionState: single source of truth
- Observer: passive execution recording
- Loss: quantify prediction error
- Learning: update Policy from Loss

The Kernel never executes capabilities.
The Kernel never manages threads.
The Kernel never performs integrations.
The Kernel only produces executable capability pipelines.
"""

from src.monkey_brain.kernel.plan.workload.workload import Workload, WorkloadStep
from src.monkey_brain.kernel.capability_interface import ICapability
from src.monkey_brain.kernel.learn.observer.observer import Observer, Observation
from src.monkey_brain.kernel.fix.loss.loss import Loss, compute_loss
from src.monkey_brain.kernel.learn.learning import Learning, Transition
from src.monkey_brain.kernel.fix.policy.policy import IPolicy, BellmanPolicy
from src.monkey_brain.kernel.fix.policy.transition import (
    Transition as RLTransition,
    TransitionTable,
)
from src.monkey_brain.kernel.fix.policy.reward import RewardModel, RewardSignal
from src.monkey_brain.kernel.learn.epa.learner import Learner, LearningMetrics
from src.monkey_brain.kernel.execute.provider.llm_explorer import (
    LLMExplorer,
    WorkflowCandidate,
)

__all__ = [
    "Workload",
    "WorkloadStep",
    "ICapability",
    "Observer",
    "Observation",
    "Loss",
    "compute_loss",
    "Learning",
    "Transition",
    "IPolicy",
    "BellmanPolicy",
    "RLTransition",
    "TransitionTable",
    "RewardModel",
    "RewardSignal",
    "Learner",
    "LearningMetrics",
    "LLMExplorer",
    "WorkflowCandidate",
]
