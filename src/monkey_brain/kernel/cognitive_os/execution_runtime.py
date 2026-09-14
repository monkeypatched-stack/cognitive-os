"""ExecutionRuntime — the Execution half of a genuinely decoupled
CognitiveOS split (Execute->ObserveOutcome->Compare->Learn->CompileΦ->
Commit), owned by the actor's cognitive engine (ComparisonIntegratedPolicy)
and exposed via CognitiveOS.execution.

Fully decoupled from ReasoningRuntime: `execute(state)` takes the
CognitiveState ReasoningRuntime.reason() produced (plan + prediction_result
already populated) and runs the remaining six stage functions against it.
No engine object is shared between the two — the CognitiveState is the
entire handoff.

Learn is deliberately NOT under ReasoningRuntime despite the originally
requested shape listing "Learning" there: the learn stage's real inputs
(comparing the Reasoning-produced prediction against the actual execution
outcome) don't exist until Execute/ObserveOutcome/Compare have already run,
so it cannot causally happen before Execution. ReasoningRuntime.
learning_policy holds the "how to learn" configuration (a genuine
reasoning-time decision); the learn stage's actual execution runs here,
using that policy object.

Agent Runtime, Workflow Engine, and Transactions are exposed honestly:
Agent Runtime references the real AgentRuntime/AgentMiddleware alias but
documents that the Execute stage doesn't call it (a real, pre-existing gap,
not something this split closes). Workflow Engine and Transactions have no
real implementation anywhere in this codebase — these are honest,
self-documented stubs, not invented behavior.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.pipeline.cognitive_policy import StageFn, run_stages
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState

logger = logging.getLogger("agentos.cognitive_os.execution_runtime")


class WorkflowEngine:
    """Honest stub — no WorkflowEngine implementation exists anywhere in
    this codebase (confirmed by exhaustive search). Returns a clearly-marked
    not-implemented result rather than a fake success."""

    async def execute(self, workflow: Any) -> dict[str, Any]:
        return {
            "status": "not_implemented",
            "reason": "WorkflowEngine has no real implementation yet — see execution_runtime.py",
        }


class TransactionManager:
    """Honest stub — no transaction concept exists anywhere in this
    codebase beyond an unused ABC (kernel/compile/solid_interfaces.py::
    TransactionalInterface, no implementers). Shaped after its begin/
    commit/rollback signatures without pretending any of the three work."""

    def begin_transaction(self, transaction_id: str) -> dict[str, Any]:
        return {"status": "not_implemented", "transaction_id": transaction_id}

    def commit(self, transaction_id: str) -> dict[str, Any]:
        return {"status": "not_implemented", "transaction_id": transaction_id}

    def rollback(self, transaction_id: str) -> dict[str, Any]:
        return {"status": "not_implemented", "transaction_id": transaction_id}


class ExecutionRuntime:
    """Execute->ObserveOutcome->Compare->Learn->CompileΦ->Commit, owned by
    the actor's cognitive engine (ComparisonIntegratedPolicy.configure()).
    Consumes the CognitiveState ReasoningRuntime.reason() produced."""

    def __init__(
        self,
        stages: list[tuple[str, StageFn]],
        *,
        capability_runtime: Any = None,
        actor_runtime: Any = None,
    ) -> None:
        self._stages = stages
        self.capability_runtime = capability_runtime
        self.workflow_engine = WorkflowEngine()
        self.transactions = TransactionManager()
        self._actor_runtime = actor_runtime

    @property
    def agent_runtime(self) -> Any:
        """The real AgentRuntime (=AgentMiddleware) alias — NOT currently
        invoked by the execute stage function below, which calls
        capability_runtime directly (see kernel/pipeline/belief_runtime.py::
        _execute_plan). Exposed for discoverability/future wiring, honestly
        documented as disconnected today."""
        from src.monkey_brain.runtime.agent_runtime import AgentRuntime

        return AgentRuntime

    @property
    def recovery(self) -> dict[str, Any]:
        """Surfaces the existing, narrower recovery mechanisms honestly
        labeled by scope, rather than merged into one fake unified thing.
        kernel/fix/self_healing/workload.py::SelfHealingPolicy is
        deliberately excluded — it's scoped to the separate GoalExecutor/
        Workload path, outside this actor-tick boundary."""
        from src.monkey_brain.kernel.pipeline.execution_runtime.retry import (
            RecoveryPolicy,
        )
        from src.monkey_brain.kernel.compile.error_recovery import (
            get_error_recovery_registry,
        )

        return {
            "step_recovery_policy": RecoveryPolicy,
            "circuit_breaker_registry": get_error_recovery_registry(),
        }

    def checkpoint(self, base_path: str) -> None:
        """Delegates to the owning ActorRuntime.checkpoint() — exposed here
        for discoverability under Execution, not reimplemented."""
        if self._actor_runtime is not None:
            self._actor_runtime.checkpoint(base_path)

    def restore(self, base_path: str) -> None:
        if self._actor_runtime is not None:
            self._actor_runtime.restore(base_path)

    async def execute(self, state: CognitiveState) -> CognitiveState:
        """Run Execute->ObserveOutcome->Compare->Learn->CompileΦ->Commit
        over the state ReasoningRuntime.reason() produced — the actual
        decoupling boundary: this is the only place Reasoning's output is
        consumed."""
        return await run_stages(state, self._stages)
