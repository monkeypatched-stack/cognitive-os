"""Execution Graph Validator — validates execution graphs before execution.

Every execution graph must satisfy:
- Directed Acyclic Graph (DAG)
- Valid dependencies
- Reachable nodes
- No orphan nodes
- Topological execution order
- Deterministic scheduling

Reject invalid graphs before execution.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentos.graph_validator")


class GraphValidationError(Exception):
    """Raised when an execution graph fails validation."""

    pass


class ExecutionGraphValidator:
    """Validates execution graphs before execution."""

    def validate(self, graph: dict[str, Any]) -> list[str]:
        """Validate an execution graph. Returns list of errors (empty = valid).

        Args:
            graph: Dict with 'nodes' and 'edges' keys

        Returns:
            List of validation error messages
        """
        errors = []

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        if not nodes:
            errors.append("Graph has no nodes")
            return errors

        # Build adjacency list and in-degree map
        node_ids = {n.get("id") for n in nodes}
        adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}

        for edge in edges:
            src = edge.get("from", "")
            dst = edge.get("to", "")

            if src not in node_ids:
                errors.append(f"Edge references unknown source node: {src}")
                continue
            if dst not in node_ids:
                errors.append(f"Edge references unknown target node: {dst}")
                continue

            adjacency[src].append(dst)
            in_degree[dst] += 1

        # Check for cycles (Kahn's algorithm)
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        visited = set()
        topo_order = []

        while queue:
            node = queue.pop(0)
            visited.add(node)
            topo_order.append(node)
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(visited) != len(node_ids):
            cycle_nodes = node_ids - visited
            errors.append(f"Graph contains cycle involving nodes: {cycle_nodes}")

        # Check for orphan nodes (no edges in or out)
        for nid in node_ids:
            has_in = any(e.get("to") == nid for e in edges)
            has_out = any(e.get("from") == nid for e in edges)
            if not has_in and not has_out and len(nodes) > 1:
                errors.append(f"Node {nid} is orphan (no edges)")

        # Check for unreachable nodes (from any root)
        roots = [nid for nid, deg in in_degree.items() if deg == 0]
        reachable = set()
        for root in roots:
            self._dfs(root, adjacency, reachable)
        unreachable = node_ids - reachable
        if unreachable:
            errors.append(f"Unreachable nodes: {unreachable}")

        # Validate node structure
        for node in nodes:
            if not node.get("id"):
                errors.append(f"Node missing 'id' field: {node}")
            if not node.get("agent") and not node.get("name"):
                errors.append(f"Node {node.get('id', '?')} missing 'agent' or 'name' field")

        return errors

    def _dfs(self, node: str, adjacency: dict[str, list[str]], visited: set):
        """Depth-first search for reachability check."""
        if node in visited:
            return
        visited.add(node)
        for neighbor in adjacency.get(node, []):
            self._dfs(neighbor, adjacency, visited)

    def validate_or_raise(self, graph: dict[str, Any]) -> None:
        """Validate graph and raise GraphValidationError if invalid."""
        errors = self.validate(graph)
        if errors:
            error_msg = "Execution graph validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            logger.error(error_msg)
            raise GraphValidationError(error_msg)

    def get_execution_order(self, graph: dict[str, Any]) -> list[list[str]]:
        """Return deterministic topological execution order (batched by level)."""
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        node_ids = {n.get("id") for n in nodes}
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
        adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}

        for edge in edges:
            src, dst = edge.get("from", ""), edge.get("to", "")
            if src in node_ids and dst in node_ids:
                adjacency[src].append(dst)
                in_degree[dst] += 1

        # Kahn's algorithm for batched topological order
        levels = []
        queue = sorted([nid for nid, deg in in_degree.items() if deg == 0])

        while queue:
            levels.append(queue[:])
            next_queue = []
            for node in queue:
                for neighbor in sorted(adjacency[node]):
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_queue.append(neighbor)
            queue = sorted(next_queue)

        return levels
