"""run_helpers.py — Shared helpers for API route handlers."""

from __future__ import annotations

import ast
import logging
from typing import Any

from fastapi import Request

from src.monkey_brain.kernel.execute.runtime.executor import (
    UnifiedExecutor,
    get_executor,
)
from src.monkey_brain.kernel.society.legacy_adapters import (
    ComparatorRuntimeAdapter,
    LegacyCognitiveRuntimeAdapter,
    SimulationRuntimeAdapter,
)

logger = logging.getLogger("agentos.api.helpers.run_helpers")


def _make_executor() -> UnifiedExecutor:
    return get_executor()


def _kernel_runtime(request: Request, runtime_id: str, state_name: str) -> Any:
    """Resolve booted runtimes through Kernel, retaining legacy app.state fallback."""
    kernel = getattr(request.app.state, "kernel", None)
    selector = getattr(kernel, "runtime_selector", None)
    if selector is not None:
        try:
            return selector.select(runtime_id)
        except LookupError:
            logger.debug("_kernel_runtime: suppressed exception", exc_info=True)
    return getattr(request.app.state, state_name, None)


def get_cognitive_runtime(request: Request) -> Any:
    """FastAPI dependency: the CognitiveRuntime booted once at app startup
    (see CognitiveRuntime.boot(), called from main.py's lifespan) — injected
    into every request instead of each route rebuilding its own pipeline.

    Returns a LegacyCognitiveRuntimeAdapter (Runtime Encapsulation Refactor,
    Phase 5) — a transparent __getattr__ passthrough wrapper, so every
    existing call site (runtime._plan(...), runtime.execute_cognitive_
    workload(...), ...) behaves identically. Only the type changes, marking
    this as a legacy runtime reached through an explicit adapter seam.
    """
    runtime = _kernel_runtime(request, "cognitive", "cognitive_runtime")
    return LegacyCognitiveRuntimeAdapter(runtime) if runtime is not None else None


def get_simulation_runtime(request: Request) -> Any:
    """FastAPI dependency: the SimulationRuntime booted once at app startup,
    completely independent of CognitiveRuntime (own app.state attribute,
    own bootstrap call — see main.py's lifespan). Wrapped in
    SimulationRuntimeAdapter — see get_cognitive_runtime's docstring.
    """
    runtime = _kernel_runtime(request, "simulation", "simulation_runtime")
    return SimulationRuntimeAdapter(runtime) if runtime is not None else None


def get_comparator_runtime(request: Request) -> Any:
    """FastAPI dependency: the ComparatorRuntime booted once at app startup,
    completely independent of CognitiveRuntime/SimulationRuntime. Wrapped in
    ComparatorRuntimeAdapter — see get_cognitive_runtime's docstring.
    """
    runtime = _kernel_runtime(request, "comparator", "comparator_runtime")
    return ComparatorRuntimeAdapter(runtime) if runtime is not None else None


def get_codegen_runtime(request: Request) -> Any:
    """FastAPI dependency: the CodeGenRuntime booted once at app startup —
    the 4th runtime, walking the SDLC as an ExecutionGraph of Broca agents.
    """
    return _kernel_runtime(request, "codegen", "codegen_runtime")


def get_process_manager(request: Request) -> Any:
    """FastAPI dependency: the Kernel's single ProcessManager, shared by
    every runtime's Runtime Processes.
    """
    return request.app.state.process_manager


def _parse_plan_answer(answer: str) -> tuple[str, list]:
    """Extract workload_id and steps from the '[plan] id: steps' answer string."""
    if not answer.startswith("[plan] "):
        return "", []
    body = answer[len("[plan] ") :]
    if ": " not in body:
        return body, []
    workload_id, steps_raw = body.split(": ", 1)
    if not steps_raw or steps_raw == "[]":
        return workload_id, []
    try:
        steps = ast.literal_eval(steps_raw)
        return workload_id, steps if isinstance(steps, list) else [steps_raw]
    except (ValueError, SyntaxError):
        return workload_id, [steps_raw]


def _get_grounding_confidence() -> float:
    """Return effective_knowledge from the cognitive kernel's knowledge pack.

    Returns 1.0 when the kernel is unavailable (neutral — don't penalise cold starts).
    """
    try:
        from src.monkey_brain.kernel.cognitive_kernel import get_cognitive_kernel

        kp = getattr(get_cognitive_kernel(), "knowledge_pack", None)
        if kp is None:
            return 1.0
        return kp.fuse().effective_knowledge
    except Exception:
        return 1.0


def build_citations(semantic_hits: list) -> list:
    """Derive citation references from semantic retrieval hits."""
    citations = []
    for hit in semantic_hits:
        if isinstance(hit, dict):
            source = hit.get("source") or hit.get("collection") or hit.get("id", "")
            score = hit.get("score", hit.get("similarity", 0.0))
        else:
            source = getattr(hit, "source", None) or getattr(hit, "collection", "") or str(hit)
            score = getattr(hit, "score", getattr(hit, "similarity", 0.0))
        if source:
            citations.append({"source": source, "score": round(float(score), 4)})
    return citations
