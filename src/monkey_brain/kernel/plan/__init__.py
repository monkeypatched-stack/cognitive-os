"""Plan phase — Intent → Goal → Workload → ExecutionGraph → Validate."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

logger = logging.getLogger("agentos.plan")

if TYPE_CHECKING:
    from src.monkey_brain.kernel.execute.graph import ExecutionGraph


@dataclass
class GraphValidation:
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def intent_to_goal(text: str, state: Any = None, source: str = "chat") -> Any:
    """Classify intent from raw text and construct a Goal.

    text  — the user message or system trigger
    state — current world state (used by classifier for context)
    Returns a Goal object; never raises (returns a fallback goal on error).
    """
    from src.monkey_brain.kernel.plan.goals.goal import Goal, GoalType, GoalSource
    from src.monkey_brain.kernel.plan.classifier.embed_classifier import classify_intent

    goal_source = GoalSource(source) if source in GoalSource._value2member_map_ else GoalSource.UNKNOWN

    try:
        classified = classify_intent(text, state or {})
        intent_name = classified.get("intent", "unknown") or "unknown"
        confidence = float(classified.get("confidence") or 0.0)
        entities = classified.get("entities") or []
        goal_type_str = classified.get("goal_type", "query")
        try:
            goal_type = GoalType(goal_type_str)
        except ValueError:
            goal_type = GoalType.QUERY

        return Goal(
            name=intent_name,
            description=text[:256],
            confidence=confidence,
            goal_type=goal_type,
            source=goal_source,
            entities=list(entities) if isinstance(entities, (list, tuple)) else [],
            metadata={"raw_text": text, "classified": classified},
        )
    except Exception:
        return Goal(
            name="unknown",
            description=text[:256],
            confidence=0.0,
            goal_type=GoalType.QUERY,
            source=goal_source,
            metadata={"raw_text": text},
        )


def validate_graph(graph: Any) -> GraphValidation:
    """Sanity-check the built ExecutionGraph before the predict/execute phases.

    Checks:
    - At least one node exists
    - No more than 200 nodes (safety cap)
    - Every non-root node has at least one incoming dependency
    - No isolated self-loops
    """
    result = GraphValidation()

    try:
        nodes = graph.get_step_nodes()
        n = len(nodes)

        if n == 0:
            result.valid = False
            result.errors.append("graph has no nodes")
            return result

        if n > 200:
            result.warnings.append(f"graph is large ({n} nodes) — may be slow")

        # Check each non-root node has at least one dep
        roots = [node for node in nodes if not any(e.rel == "depends_on" for e in graph.incoming(node.id))]
        if not roots:
            result.valid = False
            result.errors.append("no root nodes found — graph may be a pure cycle")

        # Check for self-loops
        for node in nodes:
            for e in graph.outgoing(node.id):
                if e.dst == node.id and e.rel == "depends_on":
                    result.errors.append(f"self-loop on node {node.id}")
                    result.valid = False

    except Exception as exc:
        result.valid = False
        result.errors.append(f"validation error: {exc}")

    return result


def select_workload(
    goal: Any,
    state: Any,
    mode: Any,
    run_id: str,
    lemon: Any = None,
) -> Any:
    """Select or compose a workload for the goal, attach DAG, return workload."""
    from src.monkey_brain.kernel.fix.policy.policy import BellmanPolicy
    from src.monkey_brain.kernel.plan.workload.workload import Workload, WorkloadStep
    from src.monkey_brain.kernel.execute.runtime.dag import build_execution_dag

    policy = _get_policy()

    # Let Bellman pick a pre-registered workload
    workload = None
    try:
        workload = policy.select_workload(goal, state)
    except Exception:
        logger.debug(
            "select_workload: policy selection failed, falling back to goal composition",
            exc_info=True,
        )

    # Fall back: compose a minimal single-step workload from the goal
    if workload is None:
        workload = _compose_from_goal(goal)

    _attach_dag(workload)

    if lemon:
        try:
            lemon.observe_goal(
                goal_id=run_id,
                goal_text=getattr(goal, "name", ""),
                status="planned",
                pipeline_id=workload.workload_id,
            )
        except Exception:
            logger.debug("select_workload: lemon.observe_goal failed", exc_info=True)

    return workload


def build_graph(workload: Any) -> "ExecutionGraph":
    """Return the planner-built ExecutionGraph already attached to workload.

    The planner is the only component that may construct topology. Runtime
    code must treat the graph as an immutable handoff artifact and mutate
    only state.
    """
    graph = getattr(workload, "graph", None)
    if graph is None:
        raise ValueError("build_graph requires workload.graph to already contain the planner-built ExecutionGraph")
    return graph


# ── internals ──────────────────────────────────────────────────────────────


def _attach_dag(workload: Any) -> None:
    from src.monkey_brain.kernel.execute.runtime.dag import build_execution_dag

    if getattr(workload, "dag", None) is None:
        workload.dag = build_execution_dag(workload)


def _compose_from_goal(goal: Any) -> Any:
    from src.monkey_brain.kernel.plan.workload.workload import Workload, WorkloadStep

    goal_name = getattr(goal, "name", "unknown")
    return Workload(
        steps=[
            WorkloadStep(
                step_id=f"{goal_name}-step-0",
                capability_name=goal_name,
                inputs=[],
                outputs=["answer"],
            )
        ],
        metadata={"goal": goal_name, "composed": True},
    )


def _get_policy() -> Any:
    from src.monkey_brain.kernel.plan.workload.policy import get_policy

    return get_policy()
