"""DAG — execution graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class DAGNode:
    node_id: str = field(default_factory=lambda: f"node-{uuid4().hex[:8]}")
    operator_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DAGEdge:
    source_node_id: str = ""
    target_node_id: str = ""


@dataclass
class ExecutionDAG:
    dag_id: str = field(default_factory=lambda: f"dag-{uuid4().hex[:8]}")
    nodes: list[DAGNode] = field(default_factory=list)
    edges: list[DAGEdge] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def build_execution_dag(workload: Any) -> ExecutionDAG:
    """Build an ExecutionDAG from a Workload's steps."""
    steps = getattr(workload, "steps", []) or []
    dag = ExecutionDAG(metadata={"workload_id": getattr(workload, "workload_id", "")})
    for step in steps:
        dag.nodes.append(
            DAGNode(
                node_id=step.step_id,
                operator_type=step.capability_name,
                metadata={"inputs": step.inputs, "outputs": step.outputs},
            )
        )
        for dep_id in getattr(step, "dependencies", []):
            dag.edges.append(DAGEdge(source_node_id=dep_id, target_node_id=step.step_id))
    return dag


def dag_to_execution_graph(dag: ExecutionDAG) -> "ExecutionGraph":
    """Compatibility shim for legacy callers.

    The temporal graph architecture no longer reconstructs ExecutionGraph
    from DAGs inside runtime code. Callers must pass through the planner-
    built graph directly; this helper now raises if used so that any hidden
    reconstruction path fails loudly.
    """
    raise RuntimeError(
        "dag_to_execution_graph is deprecated: planner must supply the canonical ExecutionGraph directly"
    )
