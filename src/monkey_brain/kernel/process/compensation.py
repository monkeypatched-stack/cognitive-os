"""Compensation contract and coordinator — the saga-pattern rollback mechanism.

There is no generic way to invert "send an email" or "call an external
agent" — inversion is domain knowledge only the capability author has. A
framework-level automatic undo is a category error for side-effecting
operations, so compensation is explicit and per-capability, looked up from a
registry rather than inferred.

An UNDECLARED capability is treated the same as IRREVERSIBLE by the
coordinator (not the same as NONE) — "we don't know" must never be silently
treated as "nothing to do." This is a conservative default for retrofitting
onto capabilities that predate this subsystem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from src.monkey_brain.kernel.execute.graph import ExecutionGraph, NodeState

logger = logging.getLogger("agentos.process.compensation")


class CompensationType(str, Enum):
    NONE = "none"
    """Nothing to undo (read_file, list_approvals)."""

    COMPENSATING_ACTION = "compensating_action"
    """Has a declared inverse capability."""

    IRREVERSIBLE = "irreversible"
    """Cannot be undone (send_email). Must be flagged before execution, not
    discovered as a surprise during compensation."""


@dataclass
class CompensationSpec:
    type: CompensationType
    compensating_capability: str | None = None
    compensation_inputs: Callable[[Any], dict[str, Any]] | None = None
    requires_approval_before_compensating: bool = False
    declared: bool = True
    """False only for the synthetic "undeclared" spec the registry returns
    for capabilities nobody has registered a policy for — lets the
    coordinator distinguish "audited as NONE" from "never audited"."""


UNDECLARED_SPEC = CompensationSpec(type=CompensationType.IRREVERSIBLE, declared=False)


class CompensationRegistry:
    """capability_name -> CompensationSpec. Purely additive: existing
    capabilities that never register here fall back to UNDECLARED_SPEC
    (treated as irreversible/unrecoverable by the coordinator), so nothing
    that already works today is required to change.
    """

    def __init__(self) -> None:
        self._specs: dict[str, CompensationSpec] = {}

    def register(self, capability_name: str, spec: CompensationSpec) -> None:
        self._specs[capability_name] = spec

    def get(self, capability_name: str) -> CompensationSpec:
        return self._specs.get(capability_name, UNDECLARED_SPEC)


_default_registry = CompensationRegistry()


def get_compensation_registry() -> CompensationRegistry:
    return _default_registry


def _reverse_topological_completed(graph: ExecutionGraph) -> list[Any]:
    """Completed step nodes, ordered so downstream (later-executed) nodes are
    compensated before the nodes they depended on — undo most-recent-first.
    """
    completed = [n for n in graph.get_step_nodes() if graph.get_state(n.id) == NodeState.COMPLETE]
    completed_ids = {n.id for n in completed}

    # depth = longest dependency chain ending at this node among completed nodes;
    # higher depth == executed later == undone first.
    depth: dict[str, int] = {}

    def compute_depth(node_id: str, seen: frozenset[str] = frozenset()) -> int:
        if node_id in depth:
            return depth[node_id]
        if node_id in seen:
            return 0  # defensive: cycles shouldn't exist (WorkloadDAG validates this upstream)
        preds = [e.src for e in graph.incoming(node_id) if e.rel == "depends_on" and e.src in completed_ids]
        d = 1 + max((compute_depth(p, seen | {node_id}) for p in preds), default=-1)
        depth[node_id] = d
        return d

    for n in completed:
        compute_depth(n.id)

    return sorted(completed, key=lambda n: depth[n.id], reverse=True)


async def compensate(
    graph: ExecutionGraph,
    registry: CompensationRegistry | None = None,
    execute_capability: Callable[[str, dict[str, Any]], Any] | None = None,
) -> tuple[bool, list["CompensationRecordLike"]]:
    """Walk completed nodes in reverse dependency order, invoking each one's
    declared compensating action. Returns (all_recovered, records).

    `execute_capability` is injected rather than imported, since how a
    capability actually gets invoked (CapabilityBus, direct call, etc.) is a
    decision that belongs to whoever wires the Process Manager up, not to
    this module — this stays a pure coordinator over the graph + registry.
    """
    from src.monkey_brain.kernel.process.models import CompensationRecord

    registry = registry or get_compensation_registry()
    records: list[CompensationRecord] = []
    all_recovered = True

    for node in _reverse_topological_completed(graph):
        capability_name = node.props.get("capability", node.label)
        spec = registry.get(capability_name)

        if spec.type == CompensationType.NONE:
            continue

        if spec.type == CompensationType.IRREVERSIBLE:
            all_recovered = False
            records.append(
                CompensationRecord(
                    node_id=node.id,
                    capability_name=capability_name,
                    compensating_capability=None,
                    success=False,
                    detail=(
                        "irreversible or undeclared — cannot be automatically compensated"
                        if not spec.declared
                        else "irreversible — cannot be automatically compensated"
                    ),
                )
            )
            logger.warning(
                "compensation: node=%s capability=%s cannot be undone (declared=%s)",
                node.id,
                capability_name,
                spec.declared,
            )
            continue

        # COMPENSATING_ACTION
        try:
            if execute_capability is None:
                raise RuntimeError("no execute_capability callback provided")
            inputs = spec.compensation_inputs(graph.get_result(node.id)) if spec.compensation_inputs else {}
            result = await execute_capability(spec.compensating_capability, inputs)
            records.append(
                CompensationRecord(
                    node_id=node.id,
                    capability_name=capability_name,
                    compensating_capability=spec.compensating_capability,
                    success=True,
                    detail=str(result),
                )
            )
        except Exception as exc:
            all_recovered = False
            records.append(
                CompensationRecord(
                    node_id=node.id,
                    capability_name=capability_name,
                    compensating_capability=spec.compensating_capability,
                    success=False,
                    detail=str(exc),
                )
            )
            logger.error(
                "compensation failed: node=%s capability=%s error=%s",
                node.id,
                capability_name,
                exc,
            )

    return all_recovered, records


# Type alias only for the docstring above — CompensationRecord is defined in
# models.py; importing it at module scope here would be a circular import
# (models.py doesn't need this module, but keeping the dependency direction
# one-way — compensation depends on models, not vice versa — avoids ever
# creating one).
CompensationRecordLike = Any
