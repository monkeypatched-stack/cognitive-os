"""Edge Node — represents a single edge deployment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class EdgeNode:
    """An edge node in the fleet."""

    node_id: str = field(default_factory=lambda: f"node-{uuid4().hex[:8]}")
    name: str = ""
    status: str = "online"
    version: str = "1.0.0"
    capabilities: list[str] = field(default_factory=list)
    last_heartbeat: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class EdgeNodeManager:
    """Manages edge node registration and health."""

    def __init__(self):
        self._nodes: dict[str, EdgeNode] = {}

    def register(self, node: EdgeNode) -> None:
        self._nodes[node.node_id] = node

    def heartbeat(self, node_id: str) -> bool:
        if node_id in self._nodes:
            self._nodes[node_id].last_heartbeat = datetime.now(timezone.utc).isoformat()
            return True
        return False

    def get_node(self, node_id: str) -> EdgeNode | None:
        return self._nodes.get(node_id)

    def get_all_nodes(self) -> list[EdgeNode]:
        return list(self._nodes.values())

    def get_online_nodes(self) -> list[EdgeNode]:
        return [n for n in self._nodes.values() if n.status == "online"]

    def summary(self) -> dict:
        return {
            "total_nodes": len(self._nodes),
            "online_nodes": len(self.get_online_nodes()),
        }
