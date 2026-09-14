"""Edge-Cloud Sync — synchronizes world and beliefs between edge and cloud.

Orchestrates bidirectional synchronization:
- Edge → Cloud: push edge observations to cloud world
- Cloud → Edge: pull cloud world to edge nodes

This implements Thesis 14: Edge-Cloud Cognitive Architecture.
"""

from __future__ import annotations

import logging

from src.sync.cloud_aggregator import CloudAggregator
from src.sync.edge_node import EdgeNodeManager

logger = logging.getLogger("agentos.edge_cloud_sync")


class EdgeCloudSync:
    """Synchronizes world and beliefs between edge and cloud.

    Thesis 14: Edge-Cloud Cognitive Architecture

    Cloud maintains:
        - World inference
        - Consensus world tensor
        - Shared knowledge

    Edge maintains:
        - Belief formation
        - Decision making
        - Policy
        - Learning
        - Execution

    Only observations and world updates are exchanged.
    Policies, memories, rewards and strategies remain local.
    """

    def __init__(self, cloud: CloudAggregator, edge: EdgeNodeManager) -> None:
        self._cloud = cloud
        self._edge = edge

    # ── Edge → Cloud ─────────────────────────────────────────────────────

    def sync_edge_to_cloud(self, node_id: str) -> bool:
        """Push edge observations to cloud.

        Returns True if sync was successful.
        """
        node = self._edge.get_node(node_id)
        if node is None:
            logger.warning("EdgeCloudSync: node %s not found", node_id)
            return False

        if not hasattr(node, "actor") or node.actor is None:
            logger.warning("EdgeCloudSync: node %s has no actor", node_id)
            return False

        sync_data = node.actor.sync_to_cloud()
        self._cloud.receive_edge_sync(sync_data)

        logger.info(
            "EdgeCloudSync: synced edge %s to cloud (%d observations)",
            node_id,
            len(sync_data.get("observations", [])),
        )
        return True

    def sync_all_edges_to_cloud(self) -> int:
        """Push all edge observations to cloud. Returns count of synced nodes."""
        count = 0
        for node in self._edge.get_all_nodes():
            if self.sync_edge_to_cloud(node.node_id):
                count += 1
        return count

    # ── Cloud → Edge ─────────────────────────────────────────────────────

    def sync_cloud_to_edge(self, node_id: str) -> bool:
        """Pull cloud world to edge node.

        Returns True if sync was successful.
        """
        node = self._edge.get_node(node_id)
        if node is None:
            logger.warning("EdgeCloudSync: node %s not found", node_id)
            return False

        if not hasattr(node, "actor") or node.actor is None:
            logger.warning("EdgeCloudSync: node %s has no actor", node_id)
            return False

        world_snapshot = self._cloud.get_world_snapshot()
        node.actor.receive_world_update(world_snapshot)

        logger.info(
            "EdgeCloudSync: synced cloud to edge %s (%d transitions)",
            node_id,
            len(world_snapshot.get("transitions", [])),
        )
        return True

    def sync_cloud_to_all_edges(self) -> int:
        """Pull cloud world to all edge nodes. Returns count of synced nodes."""
        count = 0
        for node in self._edge.get_all_nodes():
            if self.sync_cloud_to_edge(node.node_id):
                count += 1
        return count

    # ── Full cycle ───────────────────────────────────────────────────────

    def full_sync_cycle(self) -> dict:
        """Run a full synchronization cycle: edge → cloud → edge.

        Returns summary of the sync operation.
        """
        # 1. Push all edge observations to cloud
        edges_to_cloud = self.sync_all_edges_to_cloud()

        # 2. Pull cloud world to all edges
        cloud_to_edges = self.sync_cloud_to_all_edges()

        summary = {
            "edges_to_cloud": edges_to_cloud,
            "cloud_to_edges": cloud_to_edges,
            "world_states": len(self._cloud.world.states()),
            "world_transitions": self._cloud.world.nnz(),
        }

        logger.info(
            "EdgeCloudSync: full cycle complete — %d edges synced to cloud, cloud synced to %d edges",
            edges_to_cloud,
            cloud_to_edges,
        )

        return summary
