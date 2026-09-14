"""kernel.py:_phase_hybrid_router must wire a real PipelineOrchestrator — not the old
`getattr(cognitive, "pipeline", None)` pattern, which resolved to the live
kernel/cognitive_runtime.py:CognitiveRuntime (an incompatible .run(question: str, ...)
signature) and left every action query silently falling back to mock execution.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.monkey_brain.kernel.kernel import Kernel
from src.monkey_brain.kernel.pipeline.orchestrator import PipelineOrchestrator
from src.monkey_brain.kernel.pipeline.belief_runtime import (
    CognitiveRuntime as PipelineCognitiveRuntime,
)
from src.hybrid.query_classifier import QueryType


def test_legacy_cognitive_runtime_is_not_the_pipeline_one():
    """Step 13.1 (ACP-1): kernel/cognitive_runtime.py's class was renamed to
    LegacyCognitiveRuntime to end the naming collision with this file's own
    PipelineCognitiveRuntime import above. The bottom-of-file `CognitiveRuntime
    = LegacyCognitiveRuntime` alias keeps old imports working, but the two
    must remain genuinely distinct classes — this assertion is the regression
    guard against ever conflating them, closing the loop this test file's own
    docstring already warned about."""
    from src.monkey_brain.kernel.cognitive_runtime import (
        LegacyCognitiveRuntime,
        CognitiveRuntime,
    )

    assert CognitiveRuntime is LegacyCognitiveRuntime
    assert LegacyCognitiveRuntime is not PipelineCognitiveRuntime


@pytest.mark.asyncio
async def test_phase_hybrid_router_wires_pipeline_orchestrator():
    kernel = Kernel()
    app = SimpleNamespace(state=SimpleNamespace())

    await kernel._phase_hybrid_router(app)

    router = app.state.hybrid_router
    action_handler = router.handlers[QueryType.ACTION]

    assert isinstance(action_handler.orchestrator, PipelineOrchestrator)
    assert isinstance(action_handler.orchestrator._runtime, PipelineCognitiveRuntime)


@pytest.mark.asyncio
async def test_phase_hybrid_router_reuses_kernel_event_bus():
    kernel = Kernel()
    app = SimpleNamespace(state=SimpleNamespace())

    await kernel._phase_hybrid_router(app)

    orchestrator = app.state.hybrid_router.handlers[QueryType.ACTION].orchestrator
    assert orchestrator._runtime._event_bus is kernel.event_bus
