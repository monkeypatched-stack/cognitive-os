"""Temporal graph guards for the single-owner ExecutionGraph model."""

from __future__ import annotations

import copy
from typing import Any


class GraphTopologyError(RuntimeError):
    """Raised when a runtime changes planner-owned graph topology."""


def clone_graph_snapshot(graph: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(graph)


def assert_same_topology(
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    context: str,
) -> None:
    """Fail when graph identity or topology diverges from the planner graph."""
    checks = {
        "graph_id": (_graph_id(expected), _graph_id(actual)),
        "node_ids": (_node_ids(expected), _node_ids(actual)),
        "edge_ids": (_edge_ids(expected), _edge_ids(actual)),
        "execution_order": (_execution_order(expected), _execution_order(actual)),
    }
    for name, (left, right) in checks.items():
        if left != right:
            raise GraphTopologyError(f"{context} changed planner graph {name}: expected={left!r} actual={right!r}")


def _graph_id(graph: dict[str, Any]) -> str:
    metadata = graph.get("metadata", {}) if isinstance(graph, dict) else {}
    return str(graph.get("graph_id") or metadata.get("graph_id") or "")


def _node_ids(graph: dict[str, Any]) -> list[str]:
    return [str(node.get("id", "")) for node in graph.get("nodes", []) if isinstance(node, dict)]


def _edge_ids(graph: dict[str, Any]) -> list[tuple[str, str, str]]:
    edge_ids: list[tuple[str, str, str]] = []
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict):
            continue
        edge_ids.append(
            (
                str(edge.get("id") or ""),
                str(edge.get("from") or edge.get("src") or ""),
                str(edge.get("to") or edge.get("dst") or ""),
            )
            if edge.get("id")
            else (
                str(edge.get("from") or edge.get("src") or ""),
                str(edge.get("to") or edge.get("dst") or ""),
                str(edge.get("type") or edge.get("rel") or ""),
            )
        )
    return edge_ids


def _execution_order(graph: dict[str, Any]) -> list[str]:
    import json

    metadata = graph.get("metadata", {}) if isinstance(graph, dict) else {}
    order = graph.get("execution_order", metadata.get("execution_order", []))
    if order is None:
        return []
    if isinstance(order, list):
        return [(json.dumps(item, sort_keys=True) if isinstance(item, (list, dict)) else str(item)) for item in order]
    return [str(order)]
