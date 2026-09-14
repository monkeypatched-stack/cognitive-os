"""Control Plane — deployment, configuration, and fleet management.

The Control Plane never executes cognition.
The Control Plane never performs execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class NodeStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UPDATING = "updating"


@dataclass
class NodeInfo:
    """Information about an edge node."""

    node_id: str = field(default_factory=lambda: f"node-{uuid4().hex[:8]}")
    name: str = ""
    status: NodeStatus = NodeStatus.OFFLINE
    version: str = ""
    last_heartbeat: str = ""
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeploymentManifest:
    """Deployment manifest for edge nodes."""

    manifest_id: str = field(default_factory=lambda: f"deploy-{uuid4().hex[:8]}")
    version: str = ""
    target_nodes: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    policy_updates: dict[str, Any] = field(default_factory=dict)
    config_updates: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class ControlPlane:
    """Control Plane for AgentOS fleet management.

    Responsibilities:
    - Node registration and heartbeat
    - Deployment management
    - Configuration distribution
    - Capability distribution
    - Policy distribution
    - Fleet monitoring

    Control Plane never executes cognition.
    Control Plane never performs execution.
    """

    def __init__(self):
        self._nodes: dict[str, NodeInfo] = {}
        self._deployments: list[DeploymentManifest] = []
        self._configs: dict[str, dict[str, Any]] = {}
        self._policies: dict[str, dict[str, Any]] = {}
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    # --- Node Management ---

    def register_node(self, node_id: str, name: str = "", version: str = "", **metadata: Any) -> NodeInfo:
        node = NodeInfo(
            node_id=node_id,
            name=name,
            version=version,
            status=NodeStatus.ONLINE,
            metadata=metadata,
        )
        self._nodes[node_id] = node
        if self._lemon:
            self._lemon.counter("control_plane.nodes_registered")
        return node

    def heartbeat(self, node_id: str) -> bool:
        if node_id in self._nodes:
            self._nodes[node_id].last_heartbeat = datetime.now(timezone.utc).isoformat()
            self._nodes[node_id].status = NodeStatus.ONLINE
            return True
        return False

    def get_node(self, node_id: str) -> NodeInfo | None:
        return self._nodes.get(node_id)

    def get_all_nodes(self) -> list[NodeInfo]:
        return list(self._nodes.values())

    def get_online_nodes(self) -> list[NodeInfo]:
        return [n for n in self._nodes.values() if n.status == NodeStatus.ONLINE]

    # --- Deployment ---

    def create_deployment(
        self,
        version: str,
        target_nodes: list[str] | None = None,
        capabilities: list[str] | None = None,
        **kwargs: Any,
    ) -> DeploymentManifest:
        manifest = DeploymentManifest(
            version=version,
            target_nodes=target_nodes or list(self._nodes.keys()),
            capabilities=capabilities or [],
            **kwargs,
        )
        self._deployments.append(manifest)
        if self._lemon:
            self._lemon.counter("control_plane.deployments_created")
        return manifest

    def get_deployments(self, limit: int = 10) -> list[DeploymentManifest]:
        return self._deployments[-limit:]

    # --- Configuration ---

    def set_config(self, key: str, value: dict[str, Any]) -> None:
        self._configs[key] = value
        if self._lemon:
            self._lemon.counter("control_plane.configs_updated")

    def get_config(self, key: str) -> dict[str, Any] | None:
        return self._configs.get(key)

    def get_all_configs(self) -> dict[str, dict[str, Any]]:
        return dict(self._configs)

    # --- Policy Distribution ---

    def set_policy(self, policy_id: str, policy_data: dict[str, Any]) -> None:
        self._policies[policy_id] = policy_data
        if self._lemon:
            self._lemon.counter("control_plane.policies_updated")

    def get_policy(self, policy_id: str) -> dict[str, Any] | None:
        return self._policies.get(policy_id)

    def get_all_policies(self) -> dict[str, dict[str, Any]]:
        return dict(self._policies)

    # --- Summary ---

    def summary(self) -> dict:
        return {
            "nodes": len(self._nodes),
            "online_nodes": len(self.get_online_nodes()),
            "deployments": len(self._deployments),
            "configs": len(self._configs),
            "policies": len(self._policies),
        }
