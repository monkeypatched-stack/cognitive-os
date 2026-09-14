"""Workload Registry — manages available execution workloads.

This registry maps workload names to their executors.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

logger = logging.getLogger("agentos.workload_registry")


class Workload:
    """Thin registry entry — wraps an async executor callable.

    self.execute IS the callable; the class adds no further dispatch logic.
    """

    def __init__(self, name: str, execute_func: Callable):
        self.name = name
        self.execute: Callable = execute_func


# IMPROVEMENT: module-level mutable dict is not thread-safe under concurrent registration;
# replace with a RWLock-protected registry when throughput requires it.
_workload_registry: Dict[str, Workload] = {}


def register_workload(name: str, execute_func: Callable) -> Workload:
    """Register a new workload."""
    workload = Workload(name, execute_func)
    _workload_registry[name] = workload
    logger.info("Registered workload: %s", name)
    return workload


def get_workload(name: str) -> Optional[Workload]:
    """Get a workload by name."""
    return _workload_registry.get(name)


def list_workloads() -> list[str]:
    """List all registered workloads."""
    return list(_workload_registry.keys())


def clear_workloads():
    """Clear all registered workloads (for testing)."""
    _workload_registry.clear()


# Initialize with some default workloads
def _init_default_workloads():
    """Initialize default workloads."""

    async def default_workload_executor(context: dict) -> dict:
        """Default workload executor using workload runtime.

        If a Bellman-selected workload is present in context, execute its
        steps directly via the capability bus. Otherwise fall back to the
        workload runtime.
        """
        bellman_workload = context.get("bellman_workload")
        if bellman_workload is not None and hasattr(bellman_workload, "steps") and bellman_workload.steps:
            from src.monkey_brain.kernel.execute.cerebellum.bus import CapabilityBus

            bus = CapabilityBus()
            accumulated: dict = {}
            for step in bellman_workload.steps:
                inputs = {**context, **accumulated}
                result = await bus.execute(step.capability_name, inputs)
                if result.success:
                    accumulated.update(result.output)
            return {
                "answer": accumulated.get("answer", f"Workload {bellman_workload.workload_id} executed"),
                "semantic_hits": accumulated.get("semantic_hits", []),
                "graph_paths": accumulated.get("graph_paths", []),
                "llm_answered": accumulated.get("llm_answered", True),
            }

        goal = context.get("goal", {})
        workload_name = goal.get("name", "unknown") + "_workload"
        logger.warning(
            "No Bellman workload in context for goal %r — no fallback runtime available. "
            "Ensure policy.generate_and_select() populates context['bellman_workload'] before calling this executor.",
            workload_name,
        )
        return {
            "answer": f"No workload available for goal {workload_name!r}.",
            "semantic_hits": [],
            "graph_paths": [],
            "llm_answered": False,
        }

    # Register default workloads for common goal types
    for goal_type in [
        "batch_record",
        "work_order_query",
        "approval_query",
        "drug_research",
        "production_kpi",
        "warehouse_shipping",
    ]:
        register_workload(f"{goal_type}_workload", default_workload_executor)

    # ETASS / SittingFace codegen workload — routes directly to the sittingface handler
    async def sittingface_workload_executor(context: dict) -> dict:
        from src.monkey_brain.kernel.plan.intents.predicates.sittingface_workload import (
            sittingface_workload_question_answer,
        )

        mongo_client = context.get("mongo_client")
        question = context.get("question", "")
        result = await sittingface_workload_question_answer(mongo_client, question)
        if isinstance(result, tuple) and len(result) == 4:
            answer, semantic_hits, graph_paths, llm_answered = result
            return {
                "answer": answer,
                "semantic_hits": semantic_hits,
                "graph_paths": graph_paths,
                "llm_answered": llm_answered,
            }
        return {
            "answer": str(result),
            "semantic_hits": [],
            "graph_paths": [],
            "llm_answered": False,
        }

    register_workload("sittingface_workload_workload", sittingface_workload_executor)

    # Self-healing workload — routes directly to the self-healing predicate
    async def self_healing_workload_executor(context: dict) -> dict:
        from src.monkey_brain.kernel.plan.intents.predicates.self_healing_workload import (
            self_healing_workload_question_answer,
        )

        mongo_client = context.get("mongo_client")
        question = context.get("question", "")
        result = await self_healing_workload_question_answer(mongo_client, question)
        if isinstance(result, tuple) and len(result) == 4:
            answer, semantic_hits, graph_paths, llm_answered = result
            return {
                "answer": answer,
                "semantic_hits": semantic_hits,
                "graph_paths": graph_paths,
                "llm_answered": llm_answered,
            }
        return {
            "answer": str(result),
            "semantic_hits": [],
            "graph_paths": [],
            "llm_answered": False,
        }

    register_workload("self_healing_workload_workload", self_healing_workload_executor)


# Initialize default workloads
_init_default_workloads()
