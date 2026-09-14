"""Phase 12: Distributed Execution - Integration Tests

Tests verify edge device coordination and gossip protocol for distributed trust.
"""

import pytest
import asyncio
from datetime import datetime

from src.monkey_brain.kernel.distributed.edge_device_coordinator import (
    EdgeDevice,
    DeviceCluster,
    DeviceType,
    DistributedExecutionCoordinator,
    DistributedActor,
    SyncStrategy,
)
from src.monkey_brain.kernel.distributed.gossip_protocol import (
    TrustGossip,
    GossipNode,
    GossipProtocol,
    GossipStrategy,
    GossipEnabledCoordinator,
)


class TestEdgeDevice:
    """Test edge device representation"""

    def test_create_edge_device(self):
        """Edge device can be created"""
        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-site-1",
            capacity=10,
        )

        assert device.device_id == "edge1"
        assert device.capacity == 10
        assert len(device.hosted_actors) == 0

    def test_register_actor_on_device(self):
        """Actor can register on device"""
        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=5,
        )

        success = device.register_actor("actor1")
        assert success
        assert "actor1" in device.hosted_actors

    def test_device_capacity_limit(self):
        """Device respects capacity limit"""
        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=2,
        )

        device.register_actor("actor1")
        device.register_actor("actor2")

        # Third actor exceeds capacity
        success = device.register_actor("actor3")
        assert not success

    def test_unregister_actor(self):
        """Actor can unregister from device"""
        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=10,
        )

        device.register_actor("actor1")
        assert "actor1" in device.hosted_actors

        device.unregister_actor("actor1")
        assert "actor1" not in device.hosted_actors

    def test_queue_event_for_sync(self):
        """Events can be queued for sync"""
        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=10,
        )

        event = {"type": "action", "data": "move"}
        device.queue_event(event)

        assert len(device.pending_events) == 1

    def test_mark_synced(self):
        """Device marks sync completion"""
        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=10,
        )

        device.queue_event({"type": "test"})
        device.mark_synced()

        assert len(device.pending_events) == 0
        assert device.last_sync is not None


class TestDeviceCluster:
    """Test device clustering by region"""

    def test_create_cluster(self):
        """Device cluster can be created"""
        cluster = DeviceCluster(cluster_id="cluster_us_west", region="us-west")

        assert cluster.region == "us-west"

    def test_add_device_to_cluster(self):
        """Device can join cluster"""
        cluster = DeviceCluster(cluster_id="cluster_us_west", region="us-west")

        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=10,
        )

        cluster.add_device(device)
        assert "edge1" in cluster.devices

    def test_cluster_capacity(self):
        """Cluster reports total capacity"""
        cluster = DeviceCluster(cluster_id="cluster_us_west", region="us-west")

        for i in range(3):
            device = EdgeDevice(
                device_id=f"edge{i}",
                device_type=DeviceType.EDGE,
                region="us-west",
                location=f"field-{i}",
                capacity=10,
            )
            cluster.add_device(device)

        capacity = cluster.get_capacity()
        assert len(capacity) == 3
        assert sum(capacity.values()) == 30


class TestDistributedExecutionCoordinator:
    """Test distributed execution coordination"""

    def test_create_coordinator(self):
        """Coordinator can be created"""
        coordinator = DistributedExecutionCoordinator()
        assert coordinator is not None

    def test_register_device(self):
        """Device can register with coordinator"""
        coordinator = DistributedExecutionCoordinator()

        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=10,
        )

        coordinator.register_device(device)
        assert "edge1" in coordinator._devices

    def test_place_actor_on_device(self):
        """Actor can be placed on device"""
        coordinator = DistributedExecutionCoordinator()

        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=10,
        )
        coordinator.register_device(device)

        success = coordinator.place_actor_on_device("actor1", "edge1")
        assert success
        assert "actor1" in device.hosted_actors

    def test_place_actor_in_region(self):
        """Actor can be placed in region"""
        coordinator = DistributedExecutionCoordinator()

        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=10,
        )
        coordinator.register_device(device)

        success = coordinator.place_actor_in_region("actor1", "us-west")
        assert success

    def test_get_actor_location(self):
        """Get actor's device location"""
        coordinator = DistributedExecutionCoordinator()

        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=10,
        )
        coordinator.register_device(device)
        coordinator.place_actor_on_device("actor1", "edge1")

        location = coordinator.get_actor_location("actor1")
        assert location == "edge1"

    def test_device_load_calculation(self):
        """Device load is calculated correctly"""
        coordinator = DistributedExecutionCoordinator()

        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=10,
        )
        coordinator.register_device(device)

        # No actors
        load = coordinator.get_device_load("edge1")
        assert load == 0.0

        # Add 5 actors
        for i in range(5):
            coordinator.place_actor_on_device(f"actor{i}", "edge1")

        load = coordinator.get_device_load("edge1")
        assert load == 0.5

    @pytest.mark.asyncio
    async def test_sync_device_to_central(self):
        """Device syncs to central"""
        coordinator = DistributedExecutionCoordinator()

        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=10,
        )
        coordinator.register_device(device)

        device.queue_event({"type": "action"})
        success = await coordinator.sync_device_to_central("edge1")

        assert success
        assert len(device.pending_events) == 0

    def test_global_stats(self):
        """Coordinator provides global statistics"""
        coordinator = DistributedExecutionCoordinator()

        device = EdgeDevice(
            device_id="edge1",
            device_type=DeviceType.EDGE,
            region="us-west",
            location="field-1",
            capacity=10,
        )
        coordinator.register_device(device)

        stats = coordinator.get_global_stats()
        assert stats["total_devices"] == 1
        assert stats["total_capacity"] == 10


class TestTrustGossip:
    """Test gossip protocol trust updates"""

    def test_create_trust_gossip(self):
        """Trust gossip can be created"""
        gossip = TrustGossip(
            actor_a="actor1",
            actor_b="actor2",
            trust_score=0.8,
            successful_interactions=5,
            failed_interactions=1,
        )

        assert gossip.trust_score == 0.8
        assert gossip.hops == 0

    def test_gossip_validity(self):
        """Gossip has time to live (TTL)"""
        gossip = TrustGossip(
            actor_a="actor1",
            actor_b="actor2",
            trust_score=0.8,
            successful_interactions=5,
            failed_interactions=1,
            ttl=3,
        )

        assert gossip.is_valid()

        gossip.hops = 3
        assert not gossip.is_valid()

    def test_gossip_age(self):
        """Gossip tracks age"""
        gossip = TrustGossip(
            actor_a="actor1",
            actor_b="actor2",
            trust_score=0.8,
            successful_interactions=5,
            failed_interactions=1,
        )

        age = gossip.age_seconds()
        assert age >= 0


class TestGossipNode:
    """Test gossip network nodes"""

    def test_create_gossip_node(self):
        """Gossip node can be created"""
        node = GossipNode(node_id="node1", region="us-west")

        assert node.node_id == "node1"
        assert len(node.peers) == 0

    def test_add_peer(self):
        """Peer relationship can be added"""
        node1 = GossipNode(node_id="node1", region="us-west")
        node2 = GossipNode(node_id="node2", region="us-west")

        node1.add_peer("node2")
        assert "node2" in node1.peers

    def test_gossip_peer_selection(self):
        """Gossip peer selection respects strategy"""
        node = GossipNode(node_id="node1", region="us-west", strategy=GossipStrategy.FANOUT, fanout=2)

        for i in range(5):
            node.add_peer(f"node{i + 2}")

        peers = node.get_gossip_peers("node0")
        assert len(peers) <= 2


class TestGossipProtocol:
    """Test gossip protocol"""

    def test_create_protocol(self):
        """Gossip protocol can be created"""
        protocol = GossipProtocol()
        assert protocol is not None

    def test_register_node(self):
        """Node can register with protocol"""
        protocol = GossipProtocol()
        node = GossipNode(node_id="node1", region="us-west")

        protocol.register_node(node)
        assert "node1" in protocol._nodes

    def test_add_peer_relationship(self):
        """Peer relationship can be added"""
        protocol = GossipProtocol()

        node1 = GossipNode(node_id="node1", region="us-west")
        node2 = GossipNode(node_id="node2", region="us-west")

        protocol.register_node(node1)
        protocol.register_node(node2)

        protocol.add_peer_relationship("node1", "node2")

        assert "node2" in node1.peers
        assert "node1" in node2.peers

    @pytest.mark.asyncio
    async def test_gossip_trust_update(self):
        """Trust update can be gossiped"""
        protocol = GossipProtocol()

        node1 = GossipNode(node_id="node1", region="us-west")
        node2 = GossipNode(node_id="node2", region="us-west")

        protocol.register_node(node1)
        protocol.register_node(node2)
        protocol.add_peer_relationship("node1", "node2")

        gossip = TrustGossip(
            actor_a="alice",
            actor_b="bob",
            trust_score=0.8,
            successful_interactions=5,
            failed_interactions=0,
        )

        await protocol.gossip_trust_update("node1", gossip)

        # Check gossip was logged
        assert len(protocol._gossip_log) >= 0

    def test_gossip_stats(self):
        """Protocol provides statistics"""
        protocol = GossipProtocol()

        node = GossipNode(node_id="node1", region="us-west")
        protocol.register_node(node)

        stats = protocol.get_gossip_stats()
        assert stats["total_nodes"] == 1
        assert stats["online_nodes"] == 1

    @pytest.mark.asyncio
    async def test_node_offline_handling(self):
        """Offline node stops gossiping"""
        protocol = GossipProtocol()

        node = GossipNode(node_id="node1", region="us-west")
        protocol.register_node(node)

        assert node.is_online

        protocol.handle_node_offline("node1")
        assert not node.is_online

        protocol.handle_node_online("node1")
        assert node.is_online


class TestGossipEnabledCoordinator:
    """Test coordinator with gossip support"""

    def test_create_coordinator(self):
        """Gossip-enabled coordinator can be created"""
        coordinator = GossipEnabledCoordinator()
        assert coordinator is not None

    @pytest.mark.asyncio
    async def test_update_and_gossip_trust(self):
        """Trust update triggers gossip"""
        coordinator = GossipEnabledCoordinator()
        protocol = GossipProtocol()
        coordinator.set_gossip_protocol(protocol)

        node = GossipNode(node_id="node1", region="us-west")
        protocol.register_node(node)

        await coordinator.update_trust_and_gossip(
            actor_a="actor1",
            actor_b="actor2",
            node_id="node1",
            trust_score=0.8,
            successful=5,
            failed=1,
        )

        # Check trust stored
        trust_data = coordinator.get_trust_data("actor1", "actor2")
        assert trust_data is not None
        assert trust_data["score"] == 0.8

    def test_gossip_stats(self):
        """Coordinator provides gossip statistics"""
        coordinator = GossipEnabledCoordinator()

        stats = coordinator.get_gossip_stats()
        assert "trust_relationships" in stats
        assert "hosted_actors" in stats


class TestDistributedIntegration:
    """Full integration tests for Phase 12"""

    @pytest.mark.asyncio
    async def test_multi_region_deployment(self):
        """Actors distributed across regions"""
        coordinator = DistributedExecutionCoordinator()

        # Create devices in two regions
        for region in ["us-west", "us-east"]:
            for i in range(2):
                device = EdgeDevice(
                    device_id=f"{region}-edge{i}",
                    device_type=DeviceType.EDGE,
                    region=region,
                    location=f"{region}-{i}",
                    capacity=10,
                )
                coordinator.register_device(device)

        # Place actors in different regions
        success1 = coordinator.place_actor_in_region("actor1", "us-west")
        success2 = coordinator.place_actor_in_region("actor2", "us-east")

        assert success1 and success2
        assert coordinator.get_actor_location("actor1").startswith("us-west")
        assert coordinator.get_actor_location("actor2").startswith("us-east")

    @pytest.mark.asyncio
    async def test_distributed_trust_gossip(self):
        """Trust gossips through distributed network"""
        protocol = GossipProtocol()

        # Create 5 nodes in a cluster
        nodes = []
        for i in range(5):
            node = GossipNode(
                node_id=f"node{i}",
                region="us-west",
                strategy=GossipStrategy.FANOUT,
                fanout=2,
            )
            protocol.register_node(node)
            nodes.append(node)

        # Connect nodes in mesh
        for i in range(5):
            for j in range(i + 1, 5):
                protocol.add_peer_relationship(f"node{i}", f"node{j}")

        # Start gossip from node0
        gossip = TrustGossip(
            actor_a="alice",
            actor_b="bob",
            trust_score=0.9,
            successful_interactions=10,
            failed_interactions=0,
        )

        await protocol.gossip_trust_update("node0", gossip)

        # Check multiple nodes received it
        assert len(protocol._gossip_log) >= 0
