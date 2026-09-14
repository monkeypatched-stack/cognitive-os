"""Pipeline contracts — the stable API for the Cognitive Runtime refactor.

Dependency direction:

    contracts  ◀──  runtime
    contracts  ◀──  compiler
    contracts  ◀──  orchestrator
    contracts  ◀──  response_builder

The contracts layer defines WHAT flows through the pipeline.
The runtime layer defines HOW it is processed.

Contracts never import runtime implementations.
Runtime implementations import contracts.
"""

from src.monkey_brain.kernel.pipeline.contracts import (
    PipelineRequest,
    CompiledRequest,
    RuntimeContext,
    PipelineResponse,
)
from src.monkey_brain.kernel.pipeline.compiler import RequestCompiler
from src.monkey_brain.kernel.pipeline.orchestrator import PipelineOrchestrator
from src.monkey_brain.kernel.pipeline.response_builder import ResponseBuilder
from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime
from src.monkey_brain.kernel.pipeline.execution_state import (
    CognitiveState,
    WorldSnapshot,
)
from src.monkey_brain.kernel.pipeline.actor import Actor, ActorSnapshot
from src.monkey_brain.kernel.pipeline.cognitive_delta import CognitiveDelta
from src.monkey_brain.kernel.pipeline.cognitive_policy import CognitivePolicy
from src.monkey_brain.kernel.pipeline.planner import PlanningEngine
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
from src.monkey_brain.kernel.pipeline.plan_validator import PlanValidator
from src.monkey_brain.kernel.pipeline.execution import (
    ExecutionEngine,
    Action,
    ActionOutcome,
    ExecutionResult,
)
from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.pipeline.observations import (
    ObservationProvider,
    WorldPollingProvider,
    ObservationSet,
    Observation,
    Provenance,
    BeliefFusion,
)

__all__ = [
    "PipelineRequest",
    "CompiledRequest",
    "RuntimeContext",
    "PipelineResponse",
    "RequestCompiler",
    "PipelineOrchestrator",
    "ResponseBuilder",
    "CognitiveRuntime",
    "CognitiveState",
    "WorldSnapshot",
    "Actor",
    "ActorSnapshot",
    "CognitiveDelta",
    "CognitivePolicy",
    "ObservationProvider",
    "WorldPollingProvider",
    "ObservationSet",
    "Observation",
    "Provenance",
    "BeliefFusion",
]
