"""Canonical graph serialization helpers and graph domain schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


class CanonicalGraph(BaseModel):
    """Single canonical graph response for all runtime endpoints.

    Every endpoint returns exactly one graph. No duplicated fields.
    """

    model_config = {"extra": "allow"}

    graph_id: str = ""
    graph_type: str = "execution"
    timestamp: str = ""
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    execution_order: list[list[str]] = Field(default_factory=list)
    annotations: dict = Field(default_factory=dict)
    state: dict = Field(default_factory=dict)


@dataclass
class SemanticHit:
    collection: str = ""
    document_id: str = ""
    neo4j_node_id: str = ""
    score: float = 0.0
    text: str = ""
    labels: list[str] = field(default_factory=list)


@dataclass
class ModelGraphNode:
    node_id: str = ""
    labels: list[str] = field(default_factory=list)
    properties: dict = field(default_factory=dict)


@dataclass
class GraphRelationship:
    source_node_id: str = ""
    target_node_id: str = ""
    rel_type: str = ""
    properties: dict = field(default_factory=dict)


@dataclass
class GraphPath:
    nodes: list[GraphNode] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)


def canonical_graph_envelope(graph: Any) -> dict[str, Any]:
    """Normalize any graph-like object into the canonical graph structure.

    Returns exactly: graph_id, graph_type, timestamp, nodes, edges, execution_order.
    No duplicated fields, no extra metadata.
    """
    if hasattr(graph, "to_dict"):
        graph = graph.to_dict()
    graph = graph or {}
    if not isinstance(graph, dict):
        graph = {"state": graph}

    metadata = dict(graph.get("metadata", {}))
    execution_order = graph.get("execution_order", metadata.get("execution_order", []))
    if isinstance(execution_order, tuple):
        execution_order = list(execution_order)
    elif not isinstance(execution_order, list):
        execution_order = [execution_order] if execution_order not in (None, "") else []

    normalized_order: list[list[str]] = []
    for item in execution_order:
        if isinstance(item, list):
            normalized_order.append([str(n) for n in item])
        elif isinstance(item, str):
            normalized_order.append([item])
        elif item is not None:
            normalized_order.append([str(item)])

    return {
        "graph_id": graph.get("graph_id", metadata.get("graph_id", "")),
        "graph_type": graph.get("graph_type", metadata.get("graph_type", "execution")),
        "timestamp": graph.get("timestamp", metadata.get("timestamp", "")),
        "nodes": graph.get("nodes", []),
        "edges": graph.get("edges", []),
        "execution_order": normalized_order,
        "annotations": graph.get("annotations", metadata.get("annotations", {})),
        "state": graph.get("state", metadata.get("state", {})),
    }
