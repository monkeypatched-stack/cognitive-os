"""Agent — autonomous query handler with capability discovery.

The Agent:
1. Discovers available capabilities
2. Routes to the appropriate capability pipeline
3. Executes the pipeline
4. Returns the answer
"""

from __future__ import annotations

import logging

import time
from dataclasses import dataclass, field
from typing import Any

from monkeypatched_sdk.models import Pipeline, PipelineStep
from src.monkey_brain.kernel.execute.runtime.state import ExecutionState
from src.monkey_brain.kernel.fix.policy.policy import BellmanPolicy
from src.monkey_brain.kernel.learn.observer.observer import Observer
from src.monkey_brain.kernel.learn.learning import Learning
from src.monkey_brain.kernel.fix.policy.transition import Transition
from src.monkey_brain.kernel.plan.intents.intent_router import (
    classify_and_check_support,
)
from src.monkey_brain.runtime.runtime import Runtime
from src.introspection.lemon import Lemon

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    """Response from the Agent."""

    question: str = ""
    answer: str = ""
    intent: str = ""
    intent_confidence: float = 0.0
    supported: bool = False
    pipeline_id: str = ""
    capabilities_used: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    success: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)


class Agent:
    """Autonomous query handler with capability discovery.

    Responsibilities:
    - Discover available capabilities
    - Classify intent
    - Route to capabilities
    - Execute pipeline
    - Return answer
    """

    def __init__(
        self,
        runtime: Runtime,
        policy: BellmanPolicy | None = None,
        observer: Observer | None = None,
        learning: Learning | None = None,
        lemon: Lemon | None = None,
    ):
        self._runtime = runtime
        self._policy = policy or BellmanPolicy()
        self._observer = observer or Observer()
        self._learning = learning or Learning()
        self._lemon = lemon
        self._llm_calls = 0
        self._total_tokens = 0

    def discover_capabilities(self) -> list[str]:
        """Discover available capabilities in the runtime."""
        if hasattr(self._runtime, "available_capabilities"):
            return self._runtime.available_capabilities()
        return list(self._runtime._capabilities.keys())

    async def handle(self, question: str) -> AgentResponse:
        """Handle a question end-to-end."""
        start = time.monotonic()

        response = AgentResponse(question=question)
        state: ExecutionState | None = None
        pipeline: Pipeline | None = None

        if self._lemon:
            self._lemon.start_trace(f"agent:{question[:50]}")

        try:
            # 1. Discover capabilities
            capabilities = self.discover_capabilities()
            response.metrics["available_capabilities"] = capabilities

            # 2. Route question using classifier
            routing = classify_and_check_support(question)
            response.intent = routing.get("intent", "unknown")
            response.intent_confidence = routing.get("confidence", 0)
            response.supported = routing.get("supported", False)

            if not response.supported:
                response.answer = f"Unsupported intent: {response.intent}"
                response.latency_ms = (time.monotonic() - start) * 1000
                return response

            # 3. Create pipeline
            pipeline = Pipeline(
                steps=[
                    PipelineStep(capability_name="resolve_entity"),
                    PipelineStep(capability_name="retrieve"),
                    PipelineStep(capability_name="format"),
                ]
            )

            # 4. Policy selects pipeline
            state = ExecutionState(question=question)
            if self._policy:
                self._policy.select([pipeline], state)

            # 5. Execute pipeline
            exec_result = await self._runtime.execute(pipeline, state)

            # 6. Build response
            response.answer = exec_result.final_state.get("answer", "")
            response.pipeline_id = pipeline.pipeline_id
            response.capabilities_used = [s.capability_name for s in exec_result.steps]
            response.success = exec_result.success
            response.latency_ms = (time.monotonic() - start) * 1000

            response.metrics.update(
                {
                    "intent_confidence": response.intent_confidence,
                    "supported": response.supported,
                    "capabilities_count": len(response.capabilities_used),
                    "policy_q_entries": (self._policy.q_table_size() if self._policy else 0),
                }
            )

        except Exception as e:
            response.success = False
            response.answer = f"Error: {e}"
            response.latency_ms = (time.monotonic() - start) * 1000

        # Policy and observer updates run regardless of pipeline outcome and
        # must not overwrite a successfully computed answer if they fail.
        try:
            if self._policy and state is not None and pipeline is not None:
                self._policy.update(
                    Transition(
                        state=state.to_dict(),
                        action=pipeline.pipeline_id,
                        reward=0.95 if exec_result.success else 0.1,
                        next_state=exec_result.final_state,
                        done=True,
                    )
                )

            if state is not None:
                self._observer.observe(
                    capability="agent",
                    action="handle",
                    input_state=state.to_dict(),
                    output_state=exec_result.final_state,
                    reward=0.95 if exec_result.success else 0.1,
                    latency_ms=response.latency_ms,
                )
        except Exception as e:
            logger.debug("Policy/observer update failed: %s", e)

        if self._lemon:
            self._lemon.finish_trace()

        return response

    def summary(self) -> dict:
        return {
            "llm_calls": self._llm_calls,
            "total_tokens": self._total_tokens,
            "capabilities_discovered": len(self.discover_capabilities()),
            "observer": self._observer.summary(),
            "policy": self._policy.summary() if self._policy else None,
            "learning": self._learning.summary(),
        }
