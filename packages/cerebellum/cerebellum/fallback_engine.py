"""Fallback Engine — handles failed answers with document/web search and replanning.

When an answer fails:
1. Use document search to find relevant information
2. Use web search to find external information
3. Replan execution based on gathered information
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from cerebellum.fallback import (
    DocumentSearchCapability,
    WebSearchCapability,
    ReplannerCapability,
)

try:
    from src.monkey_brain.kernel.pipeline import Pipeline, PipelineStep
    from src.monkey_brain.kernel.execution_state import ExecutionState
    from src.monkey_brain.runtime.runtime import Runtime
except ImportError:
    Pipeline = None
    PipelineStep = None
    ExecutionState = None
    Runtime = None


@dataclass
class FallbackResult:
    """Result of a fallback execution."""

    fallback_id: str = field(default_factory=lambda: f"fb-{uuid4().hex[:8]}")
    original_answer: str = ""
    enhanced_answer: str = ""
    documents_found: int = 0
    web_results_found: int = 0
    replanned: bool = False
    success: bool = False
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class FallbackEngine:
    """Handles failed answers with document/web search and replanning.

    When an answer fails:
    1. Use document search to find relevant information
    2. Use web search to find external information
    3. Replan execution based on gathered information
    """

    def __init__(self, runtime: Runtime):
        self._runtime = runtime
        self._doc_search = DocumentSearchCapability()
        self._web_search = WebSearchCapability()
        self._replanner = ReplannerCapability()
        self._fallback_count = 0

    async def execute_fallback(
        self,
        question: str,
        original_answer: str,
        state: dict[str, Any] | None = None,
    ) -> FallbackResult:
        """Execute fallback when original answer fails."""
        import time

        start = time.monotonic()

        result = FallbackResult(
            original_answer=original_answer,
        )

        current_state = dict(state or {})
        current_state["question"] = question
        current_state["current_answer"] = original_answer

        # Step 1: Document Search
        doc_pipeline = Pipeline(steps=[PipelineStep(capability_name="document_search")])

        # Register fallback capabilities temporarily
        self._runtime.register(self._doc_search)
        self._runtime.register(self._web_search)
        self._runtime.register(self._replanner)

        doc_result = await self._runtime.execute(doc_pipeline, ExecutionState(question=question))
        if doc_result.success:
            docs = doc_result.final_state.get("documents", [])
            result.documents_found = len(docs)
            current_state["documents"] = docs

        # Step 2: Web Search
        web_pipeline = Pipeline(steps=[PipelineStep(capability_name="web_search")])

        web_result = await self._runtime.execute(web_pipeline, ExecutionState(question=question))
        if web_result.success:
            web_results = web_result.final_state.get("web_results", [])
            result.web_results_found = len(web_results)
            current_state["web_results"] = web_results

        # Step 3: Replan
        if result.documents_found > 0 or result.web_results_found > 0:
            replan_pipeline = Pipeline(steps=[PipelineStep(capability_name="replan")])

            replan_result = await self._runtime.execute(replan_pipeline, ExecutionState(question=question))
            if replan_result.success:
                result.enhanced_answer = replan_result.final_state.get("enhanced_answer", original_answer)
                result.replaned = True
                result.success = True
            else:
                result.enhanced_answer = original_answer
        else:
            result.enhanced_answer = original_answer

        result.latency_ms = (time.monotonic() - start) * 1000
        self._fallback_count += 1

        return result

    def summary(self) -> dict:
        return {
            "fallback_count": self._fallback_count,
        }
