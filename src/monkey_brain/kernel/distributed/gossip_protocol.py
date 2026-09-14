"""Phase 12: Gossip Protocol - Distributed Trust Synchronization

Enables trust relationships to propagate via peer-to-peer gossip
instead of requiring central coordination.
"""

import asyncio
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime
from enum import Enum


class GossipStrategy(str, Enum):
    """Strategies for gossip propagation"""

    RANDOM = "random"  # Random peer selection
    WEIGHTED = "weighted"  # Weighted by trust score
    FANOUT = "fanout"  # Fixed number of peers
    EPIDEMIC = "epidemic"  # Exponential spread


@dataclass
class TrustGossip:
    """Trust update to gossip"""

    actor_a: str
    actor_b: str
    trust_score: float
    successful_interactions: int
    failed_interactions: int
    timestamp: datetime = field(default_factory=datetime.now)
    source_node: str = ""
    hops: int = 0
    ttl: int = 10  # Time to live (max hops)

    def increment_hops(self) -> None:
        """Increment hop count"""
        self.hops += 1

    def is_valid(self) -> bool:
        """Check if gossip is still valid"""
        return self.hops < self.ttl

    def age_seconds(self) -> float:
        """Get age of gossip in seconds"""
        return (datetime.now() - self.timestamp).total_seconds()


@dataclass
class GossipNode:
    """Represents a node in gossip network"""

    node_id: str
    region: str
    peers: Set[str] = field(default_factory=set)
    trust_versions: Dict[Tuple[str, str], int] = field(default_factory=dict)
    last_gossip: Dict[str, datetime] = field(default_factory=dict)
    strategy: GossipStrategy = GossipStrategy.RANDOM
    fanout: int = 3  # For FANOUT strategy
    is_online: bool = True

    def add_peer(self, peer_id: str) -> None:
        """Add peer node"""
        self.peers.add(peer_id)

    def remove_peer(self, peer_id: str) -> None:
        """Remove peer node"""
        self.peers.discard(peer_id)

    def get_gossip_peers(self, sender_id: str) -> List[str]:
        """Get peers to gossip with (excluding sender)"""
        available = [p for p in self.peers if p != sender_id and p is not None]

        if self.strategy == GossipStrategy.FANOUT:
            return random.sample(available, min(self.fanout, len(available)))

        return available

    def mark_trust_version(self, actor_a: str, actor_b: str, version: int) -> None:
        """Mark version of trust relationship"""
        key = tuple(sorted([actor_a, actor_b]))
        self.trust_versions[key] = version

    def get_trust_version(self, actor_a: str, actor_b: str) -> int:
        """Get version of known trust relationship"""
        key = tuple(sorted([actor_a, actor_b]))
        return self.trust_versions.get(key, 0)


class GossipProtocol:
    """Implements gossip protocol for trust distribution

    Enables decentralized trust propagation without central coordinator.
    """

    def __init__(self):
        self._nodes: Dict[str, GossipNode] = {}
        self._gossip_log: List[TrustGossip] = []
        self._max_log_size = 5000
        self._gossip_queue: asyncio.Queue = asyncio.Queue()
        self._version_counter = 0
        self._region_clusters: Dict[str, Set[str]] = {}

    def register_node(self, node: GossipNode) -> None:
        """Register node in gossip network"""
        self._nodes[node.node_id] = node

        # Add to region cluster
        if node.region not in self._region_clusters:
            self._region_clusters[node.region] = set()
        self._region_clusters[node.region].add(node.node_id)

    def add_peer_relationship(self, node_id_a: str, node_id_b: str) -> None:
        """Add peering relationship between nodes"""
        node_a = self._nodes.get(node_id_a)
        node_b = self._nodes.get(node_id_b)

        if node_a:
            node_a.add_peer(node_id_b)
        if node_b:
            node_b.add_peer(node_id_a)

    def add_region_peers(self, region: str) -> None:
        """Make all nodes in region peers"""
        nodes_in_region = self._region_clusters.get(region, set())

        for node_id_a in nodes_in_region:
            node_a = self._nodes.get(node_id_a)
            if node_a:
                for node_id_b in nodes_in_region:
                    if node_id_a != node_id_b:
                        node_a.add_peer(node_id_b)

    async def gossip_trust_update(self, source_node_id: str, gossip: TrustGossip) -> None:
        """Start gossip of trust update from source node"""
        node = self._nodes.get(source_node_id)
        if not node or not node.is_online:
            return

        gossip.source_node = source_node_id
        gossip.hops = 0
        self._version_counter += 1

        # Mark version at source
        node.mark_trust_version(gossip.actor_a, gossip.actor_b, self._version_counter)

        # Start gossip propagation
        await self._propagate_gossip(source_node_id, gossip)

    async def _propagate_gossip(self, node_id: str, gossip: TrustGossip) -> None:
        """Propagate gossip to peers"""
        if not gossip.is_valid():
            return

        node = self._nodes.get(node_id)
        if not node or not node.is_online:
            return

        # Increment hops
        gossip.increment_hops()

        # Select peers to gossip with
        peers = node.get_gossip_peers(gossip.source_node)

        # Send to peers
        for peer_id in peers:
            peer = self._nodes.get(peer_id)
            if peer and peer.is_online:
                await self._gossip_queue.put({"to_node": peer_id, "gossip": gossip, "from_node": node_id})

                # Update peer's version
                peer.mark_trust_version(gossip.actor_a, gossip.actor_b, self._version_counter)

    async def receive_gossip(self, node_id: str, gossip: TrustGossip) -> None:
        """Node receives gossip update"""
        node = self._nodes.get(node_id)
        if not node:
            return

        # Check version
        existing_version = node.get_trust_version(gossip.actor_a, gossip.actor_b)
        if existing_version >= self._version_counter:
            return  # Already have newer version

        # Log gossip
        self._gossip_log.append(gossip)
        if len(self._gossip_log) > self._max_log_size:
            self._gossip_log = self._gossip_log[-self._max_log_size :]

        # Re-propagate to other peers (but not back to source)
        if gossip.is_valid():
            await self._propagate_gossip(node_id, gossip)

    async def get_pending_gossip(self, limit: int = 100) -> List[Dict]:
        """Get pending gossip operations"""
        items = []
        for _ in range(min(limit, self._gossip_queue.qsize())):
            try:
                item = self._gossip_queue.get_nowait()
                items.append(item)
            except asyncio.QueueEmpty:
                break
        return items

    def handle_node_offline(self, node_id: str) -> None:
        """Handle node going offline"""
        node = self._nodes.get(node_id)
        if node:
            node.is_online = False

    def handle_node_online(self, node_id: str) -> None:
        """Handle node coming back online"""
        node = self._nodes.get(node_id)
        if node:
            node.is_online = True

    def get_gossip_stats(self) -> Dict[str, any]:
        """Get gossip protocol statistics"""
        online_nodes = sum(1 for n in self._nodes.values() if n.is_online)

        return {
            "total_nodes": len(self._nodes),
            "online_nodes": online_nodes,
            "regions": len(self._region_clusters),
            "gossip_log_size": len(self._gossip_log),
            "pending_gossip": self._gossip_queue.qsize(),
            "version_counter": self._version_counter,
        }

    def get_gossip_history(self, actor_a: str = None, actor_b: str = None, limit: int = 100) -> List[TrustGossip]:
        """Get gossip history"""
        results = []

        for gossip in reversed(self._gossip_log):
            if actor_a and gossip.actor_a != actor_a:
                continue
            if actor_b and gossip.actor_b != actor_b:
                continue

            results.append(gossip)
            if len(results) >= limit:
                break

        return list(reversed(results))


class GossipEnabledCoordinator:
    """Coordinator with gossip protocol support"""

    def __init__(self):
        self._gossip_protocol = GossipProtocol()
        self._trust_data: Dict[Tuple[str, str], Dict] = {}
        self._node_to_actor_map: Dict[str, Set[str]] = {}

    def set_gossip_protocol(self, protocol: GossipProtocol) -> None:
        """Link to gossip protocol"""
        self._gossip_protocol = protocol

    async def update_trust_and_gossip(
        self,
        actor_a: str,
        actor_b: str,
        node_id: str,
        trust_score: float,
        successful: int,
        failed: int,
    ) -> None:
        """Update trust locally and gossip to network"""
        # Store locally
        key = tuple(sorted([actor_a, actor_b]))
        self._trust_data[key] = {
            "score": trust_score,
            "successful": successful,
            "failed": failed,
            "updated_at": datetime.now(),
        }

        # Create gossip
        gossip = TrustGossip(
            actor_a=actor_a,
            actor_b=actor_b,
            trust_score=trust_score,
            successful_interactions=successful,
            failed_interactions=failed,
        )

        # Propagate via gossip
        await self._gossip_protocol.gossip_trust_update(node_id, gossip)

    def get_trust_data(self, actor_a: str, actor_b: str) -> Optional[Dict]:
        """Get trust data (from gossip)"""
        key = tuple(sorted([actor_a, actor_b]))
        return self._trust_data.get(key)

    def map_actor_to_node(self, actor_id: str, node_id: str) -> None:
        """Map which node hosts which actor"""
        if node_id not in self._node_to_actor_map:
            self._node_to_actor_map[node_id] = set()
        self._node_to_actor_map[node_id].add(actor_id)

    def get_gossip_stats(self) -> Dict[str, any]:
        """Get gossip statistics"""
        stats = self._gossip_protocol.get_gossip_stats()
        stats["trust_relationships"] = len(self._trust_data)
        stats["hosted_actors"] = sum(len(actors) for actors in self._node_to_actor_map.values())
        return stats
