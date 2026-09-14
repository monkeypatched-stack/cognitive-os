"""Shared execution-graph scheduling and runtime binding helpers.

Used by ActionExecutor (actor-tick pipeline) and GraphScheduler (CodeGen/
ProcessManager) so both paths honor the same canonical ExecutionGraph model.
"""

from __future__ import annotations

from typing import Any, Iterable

from src.monkey_brain.kernel.execute.graph import ExecutionGraph, GraphNode, NodeState
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.pipeline.plan_compiler import (
    RUNTIME_CONTEXT_PROJECTIONS,
    RuntimeContextProjection,
    _runtime_projections_for,
)


def node_id_for_action(action: Action, execution_graph: ExecutionGraph) -> str | None:
    """Resolve the ExecutionGraph step node for an Action."""
    if action.step_index < 0:
        return None
    for node in execution_graph.get_step_nodes():
        if node.props.get("step_index") == action.step_index:
            return node.id
    plan_id = execution_graph.metadata.get("plan_id", action.causation_id)
    candidate = f"{plan_id}:{action.step_index}"
    return candidate if execution_graph.get_node(candidate) is not None else None


def order_actions_by_graph(
    actions: tuple[Action, ...],
    execution_graph: ExecutionGraph,
) -> tuple[Action, ...]:
    """Return actions in ExecutionGraph dependency order (topological)."""
    by_index = {a.step_index: a for a in actions if a.step_index >= 0}
    if not by_index:
        return actions

    step_nodes = execution_graph.get_step_nodes()
    node_by_index = {int(n.props.get("step_index", -1)): n for n in step_nodes if n.props.get("step_index") is not None}
    indegree: dict[int, int] = {idx: 0 for idx in by_index}
    out_edges: dict[int, list[int]] = {idx: [] for idx in by_index}
    for idx, node in node_by_index.items():
        if idx not in by_index:
            continue
        for edge in execution_graph.incoming(node.id):
            if edge.rel != "depends_on":
                continue
            src_node = execution_graph.get_node(edge.src)
            if src_node is None or src_node.type != "step":
                continue
            dep_idx = src_node.props.get("step_index")
            if dep_idx is None or dep_idx not in by_index:
                continue
            out_edges[int(dep_idx)].append(idx)
            indegree[idx] += 1

    queue = sorted(idx for idx, deg in indegree.items() if deg == 0)
    ordered: list[int] = []
    while queue:
        idx = queue.pop(0)
        ordered.append(idx)
        for nxt in sorted(out_edges.get(idx, [])):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
        queue.sort()

    if len(ordered) != len(by_index):
        return actions
    extras = [a for a in actions if a.step_index < 0]
    return tuple(extras + [by_index[i] for i in ordered])


def runnable_actions(
    actions: tuple[Action, ...],
    execution_graph: ExecutionGraph,
    succeeded_step_indices: set[int],
    *,
    completed_or_terminal: set[int] | None = None,
) -> list[Action]:
    """Actions whose graph nodes are runnable and plan-level deps are satisfied."""
    terminal = completed_or_terminal or set()
    by_index = {a.step_index: a for a in actions if a.step_index >= 0}
    runnable: list[Action] = []
    for node in execution_graph.get_step_nodes():
        idx = node.props.get("step_index")
        if idx is None or idx in terminal or idx not in by_index:
            continue
        state = execution_graph.get_state(node.id)
        if state not in (NodeState.PENDING, NodeState.READY):
            continue
        if not execution_graph._deps_satisfied(node.id):
            continue
        action = by_index[idx]
        missing = [d for d in action.depends_on if d not in succeeded_step_indices]
        if missing:
            continue
        runnable.append(action)
    runnable.sort(key=lambda a: a.step_index)
    return runnable


def apply_runtime_projections(
    result: dict[str, Any],
    context: dict[str, Any],
    projections: Iterable[RuntimeContextProjection | dict[str, Any]],
) -> None:
    """Apply compile-time projection metadata to a shared execution context."""
    for projection in projections:
        if isinstance(projection, RuntimeContextProjection):
            result_key, context_key = projection.result_key, projection.context_key
        else:
            result_key = str(projection.get("result_key", ""))
            context_key = str(projection.get("context_key", ""))
        if not result_key or not context_key:
            continue
        if result_key not in result:
            continue
        value = result[result_key]
        if context_key == "selected_product" and result_key == "selected":
            new_items = value if isinstance(value, list) else [value]
            existing = context.get("selected_product") or []
            if isinstance(existing, dict):
                existing = [existing]
            context["selected_product"] = list(existing) + new_items
        elif context_key == "order" and result_key == "order_id":
            context["order"] = result
            context["total"] = result.get("total", 0)
        elif context_key == "payment" and result_key == "payment_id":
            context["payment"] = result
        elif context_key == "delivery" and result_key == "delivery_id":
            context["delivery"] = result
        else:
            context[context_key] = value


def enrich_step_node_props(node: GraphNode) -> GraphNode:
    """Ensure step node props follow the canonical compiler convention."""
    from src.monkey_brain.kernel.execute.graph import GraphNode as GN

    props = dict(node.props or {})
    capability = props.get("capability") or node.label
    props.setdefault("capability", capability)
    if "parameters" not in props and any(
        k
        for k in props
        if k
        not in {
            "capability",
            "original_capability",
            "step_index",
            "plan_id",
            "goal_id",
            "goal",
            "actor_id",
            "description",
            "preconditions",
            "expected_outcome",
            "required_permission",
            "output_bindings",
            "input_bindings",
            "runtime_projections",
            "agent",
            "question",
        }
    ):
        props["parameters"] = {
            k: v
            for k, v in props.items()
            if k
            not in {
                "capability",
                "original_capability",
                "step_index",
                "plan_id",
                "goal_id",
                "goal",
                "actor_id",
                "description",
                "preconditions",
                "expected_outcome",
                "required_permission",
                "output_bindings",
                "input_bindings",
                "runtime_projections",
                "agent",
                "question",
            }
        }
    if "runtime_projections" not in props:
        projections = _runtime_projections_for(str(capability))
        if projections:
            props["runtime_projections"] = [
                {"result_key": p.result_key, "context_key": p.context_key} for p in projections
            ]
    return GN(id=node.id, type=node.type, label=node.label, props=props)


def normalize_execution_graph(graph: ExecutionGraph) -> ExecutionGraph:
    """Align a hand-built graph (e.g. SDLC) with the canonical compiler model."""
    from src.monkey_brain.kernel.execute.graph import GraphEdge, GraphNode

    graph.metadata.setdefault("compiler", "normalized")
    step_nodes = graph.get_step_nodes()
    capability_nodes: set[str] = set()
    for node in step_nodes:
        enriched = enrich_step_node_props(node)
        graph._nodes[node.id] = enriched
        cap = enriched.props.get("capability", enriched.label)
        cap_id = f"capability:{cap}"
        if cap_id not in capability_nodes:
            if cap_id not in graph._nodes:
                graph.add_node(GraphNode(id=cap_id, type="capability", label=cap, props={"name": cap}))
            capability_nodes.add(cap_id)
        if not any(e.rel == "uses" and e.src == node.id and e.dst == cap_id for e in graph._edges):
            graph.add_edge(GraphEdge(src=node.id, dst=cap_id, rel="uses"))

    # Runtime projection edges: producer step -> dependent consumer step.
    projections_by_node: dict[str, list[dict[str, Any]]] = {}
    for node in graph.get_step_nodes():
        projections_by_node[node.id] = list(node.props.get("runtime_projections") or [])

    for producer in graph.get_step_nodes():
        for projection in projections_by_node.get(producer.id, []):
            context_key = projection.get("context_key", "")
            for consumer in graph.get_step_nodes():
                if consumer.id == producer.id:
                    continue
                if not _depends_on(graph, consumer.id, producer.id):
                    continue
                if not _edge_exists(graph, producer.id, consumer.id, "projects_to"):
                    graph.add_edge(
                        GraphEdge(
                            src=producer.id,
                            dst=consumer.id,
                            rel="projects_to",
                            # rel only — payload lives on node props
                        )
                    )
    return graph


def _depends_on(graph: ExecutionGraph, consumer_id: str, producer_id: str) -> bool:
    for edge in graph.incoming(consumer_id):
        if edge.rel == "depends_on" and edge.src == producer_id:
            return True
    return False


def _edge_exists(graph: ExecutionGraph, src: str, dst: str, rel: str) -> bool:
    return any(e.src == src and e.dst == dst and e.rel == rel for e in graph._edges)


def step_bus_args(node: GraphNode, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build capability-bus arguments from a compiled step node."""
    props = node.props or {}
    parameters = dict(props.get("parameters") or {})
    if "description" in props and "description" not in parameters:
        parameters = {"description": props["description"], **parameters}
    args: dict[str, Any] = {
        "action": props.get("capability", node.label),
        "parameters": parameters,
    }
    if context is not None:
        args["context"] = context
    return args
