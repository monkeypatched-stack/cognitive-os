"""Agent — autonomous query handler with capability discovery.

The Agent:
1. Discovers available capabilities
2. Routes to the appropriate capability pipeline
3. Executes the pipeline
4. Returns the answer
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

try:
    from src.monkey_brain.kernel.pipeline import Pipeline, PipelineStep
    from src.monkey_brain.kernel.execution_state import ExecutionState
    from src.monkey_brain.kernel.fix.policy.policy import BellmanPolicy
    from src.monkey_brain.kernel.learn.observer.observer import Observer
    from src.monkey_brain.kernel.learn.learning import Learning
    from src.monkey_brain.kernel.fix.policy.transition import Transition
    from src.monkey_brain.kernel.plan.intents.router import classify_and_check_support
    from src.monkey_brain.runtime.runtime import Runtime
    from src.introspection.lemon import Lemon
    from src.cerebellum.fallback_engine import FallbackEngine
except ImportError:
    Pipeline = None
    PipelineStep = None
    ExecutionState = None
    BellmanPolicy = None
    Observer = None
    Learning = None
    Transition = None
    classify_and_check_support = None
    Runtime = None
    Lemon = None
    FallbackEngine = None


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
    fallback_used: bool = False
    fallback_details: dict[str, Any] = field(default_factory=dict)
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
        runtime: Runtime | None = None,
        policy: BellmanPolicy | None = None,
        observer: Observer | None = None,
        learning: Learning | None = None,
        lemon: Lemon | None = None,
    ):
        self._runtime = runtime
        self._policy = policy or (BellmanPolicy() if BellmanPolicy else None)
        self._observer = observer or (Observer() if Observer else None)
        self._learning = learning or (Learning() if Learning else None)
        self._lemon = lemon
        self._fallback_engine = FallbackEngine(runtime) if FallbackEngine and runtime else None
        self._llm_calls = 0
        self._total_tokens = 0

    def discover_capabilities(self) -> list[str]:
        """Discover available capabilities in the runtime."""
        if self._runtime and hasattr(self._runtime, "_capabilities"):
            return list(self._runtime._capabilities.keys())
        return []

    def discover_workflows(self) -> dict[str, list[str]]:
        """Discover workflow steps for each capability."""
        workflows = {}
        for cap_name in self.discover_capabilities():
            workflows[cap_name] = [cap_name]
        return workflows

    async def handle(self, question: str) -> AgentResponse:
        """Handle a question end-to-end."""
        start = time.monotonic()

        response = AgentResponse(question=question)

        if self._lemon:
            self._lemon.start_trace(f"agent:{question[:50]}")

        try:
            # 1. Discover capabilities
            capabilities = self.discover_capabilities()
            response.metrics["available_capabilities"] = capabilities

            # 2. Route question using classifier
            if classify_and_check_support:
                routing = classify_and_check_support(question)
                response.intent = routing.get("intent", "unknown")
                response.intent_confidence = routing.get("confidence", 0)
                response.supported = routing.get("supported", False)

            # 3. Create pipeline
            if Pipeline and PipelineStep:
                pipeline = Pipeline(
                    steps=[
                        PipelineStep(capability_name="resolve_entity"),
                        PipelineStep(capability_name="retrieve"),
                        PipelineStep(capability_name="format"),
                    ]
                )
            else:
                response.answer = "Pipeline not available"
                response.latency_ms = (time.monotonic() - start) * 1000
                return response

            # 4. Policy selects pipeline
            if self._policy and ExecutionState:
                self._policy.select([pipeline], ExecutionState(question=question))

            # 5. Execute pipeline
            state = ExecutionState(question=question) if ExecutionState else None
            if self._runtime:
                exec_result = await self._runtime.execute(pipeline, state)
            else:
                response.answer = "Runtime not available"
                response.latency_ms = (time.monotonic() - start) * 1000
                return response

            # 6. Build response
            response.answer = exec_result.final_state.get("answer", "")
            response.pipeline_id = pipeline.pipeline_id
            response.capabilities_used = [s.capability_name for s in exec_result.steps]
            response.success = exec_result.success
            response.latency_ms = (time.monotonic() - start) * 1000

            # 7. Check if answer needs fallback
            if self._fallback_engine and (response.answer == "No records found." or not response.answer):
                fallback_result = await self._fallback_engine.execute_fallback(
                    question=question,
                    original_answer=response.answer,
                    state=exec_result.final_state,
                )

                if fallback_result.enhanced_answer != response.answer:
                    response.answer = fallback_result.enhanced_answer
                    response.fallback_used = True
                    response.fallback_details = {
                        "documents_found": fallback_result.documents_found,
                        "web_results_found": fallback_result.web_results_found,
                        "replanned": fallback_result.replaned,
                    }

                    if fallback_result.documents_found > 0:
                        response.capabilities_used.append("document_search")
                    if fallback_result.web_results_found > 0:
                        response.capabilities_used.append("web_search")
                    if fallback_result.replaned:
                        response.capabilities_used.append("replan")

            # 8. Update policy
            if self._policy and Transition:
                self._policy.update(
                    Transition(
                        state=state.to_dict(),
                        action=pipeline.pipeline_id,
                        reward=0.95 if exec_result.success else 0.1,
                        next_state=exec_result.final_state,
                        done=True,
                    )
                )

            # 9. Observe
            if self._observer:
                self._observer.observe(
                    capability="agent",
                    action="handle",
                    input_state=state.to_dict(),
                    output_state=exec_result.final_state,
                    reward=0.95 if exec_result.success else 0.1,
                    latency_ms=response.latency_ms,
                )

            response.metrics.update(
                {
                    "intent_confidence": response.intent_confidence,
                    "supported": response.supported,
                    "capabilities_count": len(response.capabilities_used),
                    "policy_q_entries": (len(self._policy._q_table) if self._policy else 0),
                    "fallback_used": response.fallback_used,
                }
            )

        except Exception as e:
            response.success = False
            response.answer = f"Error: {e}"
            response.latency_ms = (time.monotonic() - start) * 1000

        if self._lemon:
            self._lemon.finish_trace()

        return response

    def summary(self) -> dict:
        return {
            "llm_calls": self._llm_calls,
            "total_tokens": self._total_tokens,
            "capabilities_discovered": len(self.discover_capabilities()),
            "observer": self._observer.summary() if self._observer else None,
            "policy": self._policy.summary() if self._policy else None,
            "learning": self._learning.summary() if self._learning else None,
            "fallback": (self._fallback_engine.summary() if self._fallback_engine else None),
        }
