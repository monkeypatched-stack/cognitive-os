"""Plan Compiler — the VALIDATED PLAN -> EXECUTABLE EXECUTION GRAPH boundary.

Transforms an already-selected, already-validated ``Plan`` into a
provenance-carrying ``CompiledPlanGraph`` and canonical ``ExecutionGraph``
without inventing goals, reinterpreting intent, predicting, executing
capabilities, mutating beliefs, or learning.

Compilation contract (do not violate):
    - MUST preserve every plan element verbatim: action/capability name,
      description, parameters, preconditions, depends_on, required_permission,
      step order, and optional compile-time binding declarations.
    - MUST fail explicitly before any capability is invoked when a step's
      capability cannot be resolved or the dependency/binding graph is malformed.
    - MUST NOT produce a partially valid graph and let execution discover the
      problem later.
    - Runtime output→input VALUE propagation (values that only exist after a
      capability runs) is executed via ``ActionExecutor``'s ``_context_projector``.
      Compilation records the known projection contract per capability in
      ``runtime_projections`` so graphs expose the binding without executing.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from src.monkey_brain.kernel.execute.graph import ExecutionGraph
    from src.monkey_brain.kernel.pipeline.execution import Action, ActionOutcome


@dataclass(frozen=True)
class RuntimeContextProjection:
    """Maps a capability result key onto an execution-context key at runtime."""

    result_key: str
    context_key: str


# Domain projector contracts compilation can represent without executing.
RUNTIME_CONTEXT_PROJECTIONS: dict[str, tuple[RuntimeContextProjection, ...]] = {
    "ProductSelection": (RuntimeContextProjection("selected", "selected_product"),),
    "OrderCreation": (
        RuntimeContextProjection("order_id", "order"),
        RuntimeContextProjection("total", "total"),
    ),
    "Payment": (RuntimeContextProjection("payment_id", "payment"),),
    "Delivery": (RuntimeContextProjection("delivery_id", "delivery"),),
}


@dataclass(frozen=True)
class OutputBinding:
    """A compile-time output slot declared by the planner for a step."""

    name: str
    value: Any


@dataclass(frozen=True)
class InputBinding:
    """A compile-time input slot wired to a prior step's declared output."""

    name: str
    from_step: int
    from_output: str


@dataclass(frozen=True)
class CompiledNode:
    """One compiled plan step — traceable back to its exact plan.steps index."""

    step_index: int
    node_id: str
    capability: str
    resolved_capability_name: str
    description: str
    parameters: Mapping[str, Any]
    preconditions: tuple[str, ...]
    expected_outcome: str
    depends_on: tuple[int, ...]
    required_permission: str
    agent: str = ""
    """Optional agent name from ``step.parameters["_agent"]`` when the planner
    selected one. Empty when no agent binding was declared."""
    output_bindings: tuple[OutputBinding, ...] = ()
    input_bindings: tuple[InputBinding, ...] = ()
    confidence: float = 0.0
    runtime_projections: tuple[RuntimeContextProjection, ...] = ()
    """Known result→context projections for this capability (execution-time
    wiring, represented at compile time for graph inspection)."""


@dataclass(frozen=True)
class CompiledPlanGraph:
    """The compiled, provenance-carrying representation of a validated Plan."""

    plan_id: str
    goal: str
    goal_id: str
    actor_id: str
    nodes: tuple[CompiledNode, ...]
    content_hash: str
    source_plan_step_count: int
    compiled_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CompileOutcome:
    """Result of compile_plan(): graphs on success, violations on failure."""

    graph: CompiledPlanGraph | None
    execution_graph: "ExecutionGraph | None" = None
    violations: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations


def compile_plan(
    plan: Any,
    *,
    plan_id: str,
    actor_id: str,
    goal_id: str = "",
    resolved_capabilities: Mapping[str, str | None] | None = None,
) -> CompileOutcome:
    """Compile a validated Plan into CompiledPlanGraph + ExecutionGraph."""
    steps = tuple(plan.steps) if plan is not None else ()
    n = len(steps)
    violations: list[str] = []

    if n == 0:
        violations.append("plan has no steps")

    # 1. Step validity.
    for i, step in enumerate(steps):
        action = getattr(step, "action", "") or ""
        if not str(action).strip():
            violations.append(f"step {i}: missing capability/action name")

    # 2. Capability resolvability.
    if resolved_capabilities is not None:
        for i, step in enumerate(steps):
            if resolved_capabilities.get(step.action) is None:
                violations.append(
                    f"step {i} ({step.action!r}): capability not resolvable against the wired capability bus"
                )

    # 3. depends_on range check.
    range_violation_indices: set[int] = set()
    for i, step in enumerate(steps):
        for dep in step.depends_on:
            if dep < 0 or dep >= n:
                violations.append(
                    f"step {i} ({step.action!r}): depends_on references out-of-range index {dep} (plan has {n} steps)"
                )
                range_violation_indices.add(i)

    # 4. Cycle detection — only when every edge endpoint exists.
    if n and not range_violation_indices:
        cycle = _find_cycle(steps)
        if cycle is not None:
            path = " -> ".join(f"{idx}({steps[idx].action})" for idx in cycle)
            violations.append(f"circular dependency: {path}")

    # 5. Binding extraction + validation (needs valid step indices).
    binding_specs: list[tuple[tuple[OutputBinding, ...], tuple[InputBinding, ...]]] = []
    if n and not range_violation_indices:
        for i, step in enumerate(steps):
            outputs, inputs = _extract_bindings(step.parameters)
            for binding in inputs:
                if binding.from_step < 0 or binding.from_step >= n:
                    violations.append(
                        f"step {i} ({step.action!r}): input binding "
                        f"{binding.name!r} references out-of-range step "
                        f"{binding.from_step}"
                    )
                elif binding.from_step == i:
                    violations.append(
                        f"step {i} ({step.action!r}): input binding {binding.name!r} cannot reference itself"
                    )
            binding_specs.append((outputs, inputs))

        if not violations:
            declared_outputs: dict[int, dict[str, Any]] = {}
            for i, (outputs, _) in enumerate(binding_specs):
                declared_outputs[i] = {b.name: b.value for b in outputs}
            for i, (_, inputs) in enumerate(binding_specs):
                for binding in inputs:
                    source_outputs = declared_outputs.get(binding.from_step, {})
                    if binding.from_output not in source_outputs:
                        violations.append(
                            f"step {i} ({steps[i].action!r}): input binding "
                            f"{binding.name!r} references undeclared output "
                            f"{binding.from_output!r} from step {binding.from_step}"
                        )

    if violations:
        return CompileOutcome(graph=None, execution_graph=None, violations=tuple(violations))

    resolved_goal_id = goal_id or _goal_id_from_plan(plan)
    nodes = tuple(
        CompiledNode(
            step_index=i,
            node_id=f"{plan_id}:{i}",
            capability=step.action,
            resolved_capability_name=(
                resolved_capabilities.get(step.action, step.action)
                if resolved_capabilities is not None
                else step.action
            ),
            description=step.description,
            parameters=MappingProxyType(dict(step.parameters)),
            preconditions=tuple(step.preconditions),
            expected_outcome=step.expected_outcome,
            depends_on=tuple(step.depends_on),
            required_permission=step.required_permission,
            agent=str(step.parameters.get("_agent", "") or ""),
            output_bindings=binding_specs[i][0],
            input_bindings=binding_specs[i][1],
            confidence=float(getattr(step, "confidence", 0.0) or 0.0),
            runtime_projections=_runtime_projections_for(
                resolved_capabilities.get(step.action, step.action)
                if resolved_capabilities is not None
                else step.action
            ),
        )
        for i, step in enumerate(steps)
    )

    goal = getattr(plan, "goal", "") or ""
    content_hash = _content_hash(plan_id, actor_id, goal, resolved_goal_id, nodes)

    graph = CompiledPlanGraph(
        plan_id=plan_id,
        goal=goal,
        goal_id=resolved_goal_id,
        actor_id=actor_id,
        nodes=nodes,
        content_hash=content_hash,
        source_plan_step_count=n,
    )
    execution_graph = build_execution_graph(graph)
    return CompileOutcome(graph=graph, execution_graph=execution_graph, violations=())


def build_execution_graph(compiled: CompiledPlanGraph) -> "ExecutionGraph":
    """Materialize a canonical ExecutionGraph from a CompiledPlanGraph."""
    from src.monkey_brain.kernel.execute.graph import (
        ExecutionGraph,
        GraphEdge,
        GraphNode,
    )

    graph = ExecutionGraph(id=f"graph-{compiled.plan_id}")
    graph.metadata = {
        "plan_id": compiled.plan_id,
        "goal_id": compiled.goal_id,
        "goal": compiled.goal,
        "actor_id": compiled.actor_id,
        "content_hash": compiled.content_hash,
        "compiler": "plan_compiler",
        "source_plan_step_count": compiled.source_plan_step_count,
    }

    capability_nodes: set[str] = set()
    for node in compiled.nodes:
        props = {
            "capability": node.resolved_capability_name,
            "original_capability": node.capability,
            "step_index": node.step_index,
            "plan_id": compiled.plan_id,
            "goal_id": compiled.goal_id,
            "goal": compiled.goal,
            "actor_id": compiled.actor_id,
            "description": node.description,
            "parameters": dict(node.parameters),
            "preconditions": list(node.preconditions),
            "expected_outcome": node.expected_outcome,
            "required_permission": node.required_permission,
            "output_bindings": [{"name": b.name, "value": b.value} for b in node.output_bindings],
            "input_bindings": [
                {
                    "name": b.name,
                    "from_step": b.from_step,
                    "from_output": b.from_output,
                    "from_node": f"{compiled.plan_id}:{b.from_step}",
                }
                for b in node.input_bindings
            ],
            "runtime_projections": [
                {"result_key": p.result_key, "context_key": p.context_key} for p in node.runtime_projections
            ],
        }
        if node.agent:
            props["agent"] = node.agent
        graph.add_node(
            GraphNode(
                id=node.node_id,
                type="step",
                label=node.capability,
                props=props,
            )
        )

        cap_id = f"capability:{node.resolved_capability_name}"
        if cap_id not in capability_nodes:
            graph.add_node(
                GraphNode(
                    id=cap_id,
                    type="capability",
                    label=node.resolved_capability_name,
                    props={"name": node.resolved_capability_name},
                )
            )
            capability_nodes.add(cap_id)
        graph.add_edge(GraphEdge(src=node.node_id, dst=cap_id, rel="uses"))

        if node.agent:
            agent_id = f"agent:{node.agent}"
            if agent_id not in graph._nodes:
                graph.add_node(
                    GraphNode(
                        id=agent_id,
                        type="agent",
                        label=node.agent,
                        props={"agent": node.agent},
                    )
                )
            graph.add_edge(GraphEdge(src=agent_id, dst=cap_id, rel="provides"))

    for node in compiled.nodes:
        for dep_idx in node.depends_on:
            graph.add_edge(
                GraphEdge(
                    src=f"{compiled.plan_id}:{dep_idx}",
                    dst=node.node_id,
                    rel="depends_on",
                )
            )
        for binding in node.input_bindings:
            graph.add_edge(
                GraphEdge(
                    src=f"{compiled.plan_id}:{binding.from_step}",
                    dst=node.node_id,
                    rel="binds",
                )
            )

    _add_runtime_projection_edges(graph, compiled)

    graph.metadata["execution_order"] = [n.node_id for n in compiled.nodes]
    graph.sign()
    return graph


def _add_runtime_projection_edges(graph: "ExecutionGraph", compiled: CompiledPlanGraph) -> None:
    """Explicit runtime output→context→input edges between dependent steps."""
    from src.monkey_brain.kernel.execute.graph import GraphEdge

    nodes_by_index = {n.step_index: n for n in compiled.nodes}
    for producer in compiled.nodes:
        if not producer.runtime_projections:
            continue
        for consumer in compiled.nodes:
            if consumer.step_index == producer.step_index:
                continue
            if producer.step_index not in consumer.depends_on:
                continue
            for projection in producer.runtime_projections:
                if any(
                    e.src == producer.node_id and e.dst == consumer.node_id and e.rel == "projects_to"
                    for e in graph._edges
                ):
                    continue
                graph.add_edge(
                    GraphEdge(
                        src=producer.node_id,
                        dst=consumer.node_id,
                        rel="projects_to",
                    )
                )


def graphs_equivalent(left: "ExecutionGraph", right: "ExecutionGraph") -> bool:
    """Compare two ExecutionGraph instances for structural/semantic equality."""
    if left.id != right.id:
        return False
    if left.node_count != right.node_count or left.edge_count != right.edge_count:
        return False
    left_nodes = {n.id: n for n in left.all_nodes()}
    right_nodes = {n.id: n for n in right.all_nodes()}
    if set(left_nodes) != set(right_nodes):
        return False
    for node_id, ln in left_nodes.items():
        rn = right_nodes[node_id]
        if ln.type != rn.type or ln.label != rn.label or dict(ln.props) != dict(rn.props):
            return False
    left_edges = sorted((e.src, e.dst, e.rel) for e in left._edges)
    right_edges = sorted((e.src, e.dst, e.rel) for e in right._edges)
    return left_edges == right_edges


def _goal_id_from_plan(plan: Any) -> str:
    metadata = getattr(plan, "metadata", None) or {}
    if isinstance(metadata, Mapping):
        return str(metadata.get("goal_id", "") or "")
    return ""


def _extract_bindings(
    parameters: Mapping[str, Any] | dict[str, Any],
) -> tuple[tuple[OutputBinding, ...], tuple[InputBinding, ...]]:
    raw = parameters.get("_bindings", {}) if parameters else {}
    if not isinstance(raw, Mapping):
        return (), ()

    outputs: list[OutputBinding] = []
    for name, value in sorted((raw.get("outputs") or {}).items(), key=lambda item: str(item[0])):
        outputs.append(OutputBinding(name=str(name), value=value))

    inputs: list[InputBinding] = []
    for name, spec in sorted((raw.get("inputs") or {}).items(), key=lambda item: str(item[0])):
        if not isinstance(spec, Mapping):
            continue
        inputs.append(
            InputBinding(
                name=str(name),
                from_step=int(spec.get("from_step", -1)),
                from_output=str(spec.get("from_output", "") or ""),
            )
        )
    return tuple(outputs), tuple(inputs)


def _find_cycle(steps: tuple) -> tuple[int, ...] | None:
    n = len(steps)
    indegree = [0] * n
    out_edges: list[list[int]] = [[] for _ in range(n)]
    for i, step in enumerate(steps):
        for dep in step.depends_on:
            out_edges[dep].append(i)
            indegree[i] += 1

    queue = [i for i in range(n) if indegree[i] == 0]
    processed: list[int] = []
    queue.sort()
    while queue:
        node = queue.pop(0)
        processed.append(node)
        newly_ready = []
        for nxt in out_edges[node]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                newly_ready.append(nxt)
        queue.extend(sorted(newly_ready))
        queue.sort()

    if len(processed) == n:
        return None

    leftover = sorted(set(range(n)) - set(processed))
    return _reconstruct_cycle(steps, leftover[0], set(leftover))


def _reconstruct_cycle(steps: tuple, start: int, leftover: set[int]) -> tuple[int, ...]:
    path: list[int] = []
    on_path: dict[int, int] = {}
    node = start
    while True:
        if node in on_path:
            start_pos = on_path[node]
            return tuple(path[start_pos:] + [node])
        on_path[node] = len(path)
        path.append(node)
        deps = [d for d in steps[node].depends_on if d in leftover]
        node = min(deps)


def _content_hash(
    plan_id: str,
    actor_id: str,
    goal: str,
    goal_id: str,
    nodes: tuple[CompiledNode, ...],
) -> str:
    payload = {
        "plan_id": plan_id,
        "actor_id": actor_id,
        "goal": goal,
        "goal_id": goal_id,
        "nodes": [
            {
                "step_index": node.step_index,
                "node_id": node.node_id,
                "capability": node.capability,
                "resolved_capability_name": node.resolved_capability_name,
                "description": node.description,
                "parameters": dict(node.parameters),
                "preconditions": list(node.preconditions),
                "expected_outcome": node.expected_outcome,
                "depends_on": list(node.depends_on),
                "required_permission": node.required_permission,
                "agent": node.agent,
                "confidence": node.confidence,
                "output_bindings": [{"name": b.name, "value": b.value} for b in node.output_bindings],
                "input_bindings": [
                    {
                        "name": b.name,
                        "from_step": b.from_step,
                        "from_output": b.from_output,
                    }
                    for b in node.input_bindings
                ],
                "runtime_projections": [
                    {"result_key": p.result_key, "context_key": p.context_key} for p in node.runtime_projections
                ],
            }
            for node in nodes
        ],
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def broca_dict_to_plan(
    graph: Any,
    *,
    goal: str = "",
    goal_id: str = "",
) -> Any:
    """Convert a Broca/HTTP planner dict graph into a belief_state Plan."""
    from src.monkey_brain.kernel.pipeline.belief_state import Plan, PlanStep

    if graph is None:
        return Plan(goal=goal or "", steps=(), metadata={"goal_id": goal_id})

    if hasattr(graph, "to_dict"):
        graph = graph.to_dict()
    elif hasattr(graph, "nodes") and hasattr(graph, "edges"):
        graph = {
            "nodes": list(getattr(graph, "nodes", [])),
            "edges": list(getattr(graph, "edges", [])),
            "metadata": dict(getattr(graph, "metadata", {})),
        }

    metadata = graph.get("metadata", {}) if isinstance(graph, Mapping) else {}
    resolved_goal = goal or metadata.get("goal") or graph.get("domain", "") or "planned"
    resolved_goal_id = goal_id or metadata.get("goal_id") or metadata.get("run_id", "")

    nodes = [n.to_dict() if hasattr(n, "to_dict") else n for n in graph.get("nodes", [])]
    nodes = [n for n in nodes if isinstance(n, dict)]
    by_id = {str(n.get("id", "")): n for n in nodes if n.get("id")}

    deps: dict[str, list[str]] = {nid: [] for nid in by_id}
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict):
            continue
        rel = str(edge.get("type") or edge.get("rel") or "depends_on")
        if rel not in ("depends_on", "depends", ""):
            continue
        src = str(edge.get("from") or edge.get("src") or "")
        dst = str(edge.get("to") or edge.get("dst") or "")
        if src in by_id and dst in by_id:
            deps[dst].append(src)

    ordered = _order_broca_nodes(nodes, by_id, deps, graph.get("execution_order", []))
    id_to_index = {str(node.get("id", "")): i for i, node in enumerate(ordered)}

    steps: list[PlanStep] = []
    for node in ordered:
        node_id = str(node.get("id", ""))
        props = node.get("props", node.get("metadata", {})) or {}
        if not isinstance(props, dict):
            props = {}
        agent_name = str(node.get("agent") or props.get("agent") or "")
        capability = (
            agent_name
            or str(node.get("capability_name") or props.get("capability") or "")
            or str(node.get("label") or node.get("name") or node_id)
        )
        parameters = dict(props.get("parameters", props))
        if agent_name:
            parameters.setdefault("_agent", agent_name)
        dep_indices = tuple(sorted(id_to_index[dep] for dep in deps.get(node_id, []) if dep in id_to_index))
        steps.append(
            PlanStep(
                action=capability,
                description=str(node.get("label") or node.get("name") or capability),
                parameters=parameters,
                depends_on=dep_indices,
            )
        )

    return Plan(
        goal=resolved_goal,
        steps=tuple(steps),
        planner="broca",
        metadata={"goal_id": resolved_goal_id, "source": "broca_dict"},
    )


def compile_broca_graph(
    graph: Any,
    *,
    plan_id: str,
    actor_id: str,
    goal_id: str = "",
    goal: str = "",
    resolved_capabilities: Mapping[str, str | None] | None = None,
) -> CompileOutcome:
    """Bridge HTTP/Broca dict graphs through the canonical plan compiler."""
    plan = broca_dict_to_plan(graph, goal=goal, goal_id=goal_id or plan_id)
    return compile_plan(
        plan,
        plan_id=plan_id,
        actor_id=actor_id,
        goal_id=goal_id or plan_id,
        resolved_capabilities=resolved_capabilities,
    )


def execution_graph_to_plan_steps(
    execution_graph: "ExecutionGraph",
) -> list[dict[str, Any]]:
    """Convert a compiled ExecutionGraph into GoalExecutor-compatible step dicts."""
    step_nodes = execution_graph.get_step_nodes()
    by_id = {n.id: n for n in step_nodes}
    deps: dict[str, list[str]] = {nid: [] for nid in by_id}
    for edge in execution_graph._edges:
        if edge.rel == "depends_on" and edge.src in by_id and edge.dst in by_id:
            deps[edge.dst].append(edge.src)

    ordered = _order_broca_nodes(
        [{"id": n.id} for n in step_nodes],
        {n.id: {"id": n.id} for n in step_nodes},
        deps,
        execution_graph.metadata.get("execution_order", []),
    )
    ordered_nodes = [by_id[str(n["id"])] for n in ordered]

    steps: list[dict[str, Any]] = []
    for node in ordered_nodes:
        props = dict(node.props or {})
        steps.append(
            {
                "step_id": node.id,
                "name": node.label,
                "capability_name": props.get("capability", node.label),
                "agent": props.get("agent", ""),
                "step_type": "agent",
                "dependencies": list(deps.get(node.id, [])),
                "metadata": props,
            }
        )
    return steps


def build_actions_from_compiled(
    compiled: CompiledPlanGraph,
    *,
    plan_id: str,
    actor_id: str,
    execution_id: str,
    resolved_permissions: Any = (),
    no_permission_actions: frozenset[str] | set[str] = frozenset(),
) -> tuple[tuple["Action", ...], dict[int, "ActionOutcome"]]:
    """Build dispatch Actions from a CompiledPlanGraph (graph-driven dispatch)."""
    from src.monkey_brain.kernel.pipeline.execution import Action, ActionOutcome

    actions: list[Action] = []
    denied: dict[int, ActionOutcome] = {}
    for node in compiled.nodes:
        action_id = f"{actor_id}_step_{node.step_index}" if actor_id else f"step_{node.step_index}"
        if (
            node.required_permission
            and node.capability not in no_permission_actions
            and node.required_permission not in resolved_permissions
        ):
            denied[node.step_index] = ActionOutcome(
                action_id=action_id,
                success=False,
                result={
                    "required_permission": node.required_permission,
                    "not_attempted": True,
                },
                error=f"Permission denied: missing {node.required_permission}",
            )
            continue
        actions.append(
            Action(
                action_id=action_id,
                capability=node.capability,
                parameters={"description": node.description, **dict(node.parameters)},
                preconditions=node.preconditions,
                expected_outcome=node.expected_outcome,
                confidence=node.confidence,
                source_step=node.capability,
                correlation_id=execution_id,
                causation_id=plan_id,
                step_index=node.step_index,
                depends_on=node.depends_on,
            )
        )
    return tuple(actions), denied


def _runtime_projections_for(
    capability_name: str,
) -> tuple[RuntimeContextProjection, ...]:
    return RUNTIME_CONTEXT_PROJECTIONS.get(capability_name, ())


def _order_broca_nodes(
    nodes: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    deps: dict[str, list[str]],
    execution_order: Any,
) -> list[dict[str, Any]]:
    """Topological ordering shared by Broca conversion and graph export."""
    if execution_order:
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        layers = execution_order if isinstance(execution_order[0], list) else [execution_order]
        for layer in layers:
            for node_id in layer:
                nid = str(node_id)
                if nid in by_id and nid not in seen:
                    ordered.append(by_id[nid])
                    seen.add(nid)
        for node in nodes:
            nid = str(node.get("id", ""))
            if nid in by_id and nid not in seen:
                ordered.append(by_id[nid])
        if ordered:
            return ordered

    indegree = {nid: 0 for nid in by_id}
    for dst, srcs in deps.items():
        if dst not in by_id:
            continue
        for src in srcs:
            if src in by_id:
                indegree[dst] += 1

    queue = sorted(nid for nid, deg in indegree.items() if deg == 0)
    ordered_ids: list[str] = []
    remaining = dict(indegree)
    out_adj: dict[str, list[str]] = {nid: [] for nid in by_id}
    for dst, srcs in deps.items():
        for src in srcs:
            if src in by_id and dst in by_id:
                out_adj[src].append(dst)

    while queue:
        nid = queue.pop(0)
        ordered_ids.append(nid)
        for nxt in sorted(out_adj.get(nid, [])):
            remaining[nxt] -= 1
            if remaining[nxt] == 0:
                queue.append(nxt)
                queue.sort()

    if len(ordered_ids) != len(by_id):
        return nodes
    return [by_id[nid] for nid in ordered_ids if nid in by_id]
