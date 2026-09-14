"""Tests for Edge-Cloud Architecture (Thesis 14)."""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.compile.tensor import Feature
from src.sync.edge_node import EdgeNode, EdgeNodeManager
from src.sync.edge_actor import EdgeActor
from src.sync.cloud_aggregator import CloudAggregator
from src.sync.edge_cloud_sync import EdgeCloudSync


class TestEdgeNode:
    """EdgeNode data model."""

    def test_create_edge_node(self):
        node = EdgeNode(name="factory-sensor")
        assert node.name == "factory-sensor"
        assert node.status == "online"

    def test_edge_node_manager(self):
        manager = EdgeNodeManager()
        node = EdgeNode(name="node-1")
        manager.register(node)

        assert manager.get_node(node.node_id) is not None
        assert len(manager.get_all_nodes()) == 1

    def test_heartbeat(self):
        manager = EdgeNodeManager()
        node = EdgeNode(name="node-1")
        manager.register(node)

        assert manager.heartbeat(node.node_id) is True
        assert node.last_heartbeat != ""

    def test_online_nodes(self):
        manager = EdgeNodeManager()
        manager.register(EdgeNode(name="online-1", status="online"))
        manager.register(EdgeNode(name="offline-1", status="offline"))

        assert len(manager.get_online_nodes()) == 1


class TestEdgeActor:
    """EdgeActor with local belief and policy."""

    def test_create_edge_actor(self):
        actor = EdgeActor("actor-1", "node-1")
        assert actor.actor_id == "actor-1"
        assert actor.node_id == "node-1"

    def test_observe_updates_belief(self):
        actor = EdgeActor("actor-1", "node-1")
        actor.observe("s1", "s2")

        assert actor.belief.nnz() == 1

    def test_learn_updates_policy(self):
        actor = EdgeActor("actor-1", "node-1")
        actor.learn("s1", "action_1", reward=0.8)

        # Policy should be updated
        assert actor.policy.value("s1", "action_1") > 0.5

    def test_sync_to_cloud(self):
        actor = EdgeActor("actor-1", "node-1")
        actor.observe("s1", "s2")
        actor.learn("s1", "action_1", reward=0.8)

        sync_data = actor.sync_to_cloud()
        assert sync_data["actor_id"] == "actor-1"
        assert sync_data["node_id"] == "node-1"
        assert len(sync_data["observations"]) == 1
        assert sync_data["policy_snapshot"] is not None

    def test_receive_world_update(self):
        actor = EdgeActor("actor-1", "node-1")
        world_snapshot = {
            "transitions": [
                {"src": "s1", "dst": "s2", "domain": "manufacturing"},
                {"src": "s2", "dst": "s3", "domain": "manufacturing"},
            ]
        }
        actor.receive_world_update(world_snapshot)

        assert actor.belief.nnz() == 2


class TestCloudAggregator:
    """CloudAggregator with world tensor."""

    def test_create_cloud_aggregator(self):
        cloud = CloudAggregator()
        assert cloud.world.nnz() == 0

    def test_receive_edge_sync(self):
        cloud = CloudAggregator()
        sync_data = {
            "node_id": "node-1",
            "observations": [
                {"src": "s1", "dst": "s2", "domain": "manufacturing"},
                {"src": "s2", "dst": "s3", "domain": "manufacturing"},
            ],
        }
        cloud.receive_edge_sync(sync_data)

        assert cloud.world.nnz() == 2
        assert cloud.world_learner.update_count == 2

    def test_get_world_snapshot(self):
        cloud = CloudAggregator()
        cloud.receive_edge_sync(
            {
                "node_id": "node-1",
                "observations": [{"src": "s1", "dst": "s2"}],
            }
        )

        snapshot = cloud.get_world_snapshot()
        assert "transitions" in snapshot
        assert len(snapshot["transitions"]) == 1

    def test_fleet_metrics(self):
        cloud = CloudAggregator()
        cloud.record_node_metrics("node-1", {"executions": 10, "success_rate": 0.9})
        cloud.record_node_metrics("node-2", {"executions": 5, "success_rate": 0.8})

        fleet = cloud.aggregate_fleet()
        assert fleet.total_nodes == 2
        assert fleet.total_executions == 15


class TestEdgeCloudSync:
    """EdgeCloudSync bidirectional synchronization."""

    def test_sync_edge_to_cloud(self):
        edge = EdgeNodeManager()
        cloud = CloudAggregator()
        sync = EdgeCloudSync(cloud, edge)

        # Create edge node with actor
        node = EdgeNode(name="node-1")
        actor = EdgeActor("actor-1", node.node_id)
        node.actor = actor
        edge.register(node)

        # Actor observes something
        actor.observe("s1", "s2")

        # Sync to cloud
        assert sync.sync_edge_to_cloud(node.node_id) is True
        assert cloud.world.nnz() == 1

    def test_sync_cloud_to_edge(self):
        edge = EdgeNodeManager()
        cloud = CloudAggregator()
        sync = EdgeCloudSync(cloud, edge)

        # Cloud has some world data
        cloud.receive_edge_sync(
            {
                "node_id": "cloud",
                "observations": [
                    {"src": "s1", "dst": "s2"},
                    {"src": "s2", "dst": "s3"},
                ],
            }
        )

        # Create edge node with actor
        node = EdgeNode(name="node-1")
        actor = EdgeActor("actor-1", node.node_id)
        node.actor = actor
        edge.register(node)

        # Sync cloud to edge
        assert sync.sync_cloud_to_edge(node.node_id) is True
        assert actor.belief.nnz() == 2

    def test_full_sync_cycle(self):
        edge = EdgeNodeManager()
        cloud = CloudAggregator()
        sync = EdgeCloudSync(cloud, edge)

        # Create two edge nodes
        node1 = EdgeNode(name="node-1")
        actor1 = EdgeActor("actor-1", node1.node_id)
        node1.actor = actor1
        edge.register(node1)

        node2 = EdgeNode(name="node-2")
        actor2 = EdgeActor("actor-2", node2.node_id)
        node2.actor = actor2
        edge.register(node2)

        # Each actor observes different things
        actor1.observe("s1", "s2")
        actor2.observe("s3", "s4")

        # Run full sync cycle
        result = sync.full_sync_cycle()

        assert result["edges_to_cloud"] == 2
        assert result["cloud_to_edges"] == 2
        # Cloud should have both observations
        assert cloud.world.nnz() == 2
        # Both edge actors should have cloud's world
        assert actor1.belief.nnz() == 2
        assert actor2.belief.nnz() == 2


class TestThesis14Verification:
    """Verify Thesis 14: Edge-Cloud Cognitive Architecture."""

    def test_cloud_maintains_world_inference(self):
        """Cloud maintains world inference and consensus world tensor."""
        cloud = CloudAggregator()
        assert cloud.world is not None
        assert cloud.world_learner is not None

    def test_edge_maintains_belief_and_policy(self):
        """Edge maintains belief formation, decision making, policy, learning."""
        actor = EdgeActor("actor-1", "node-1")
        assert actor.belief is not None  # Belief formation
        assert actor.policy is not None  # Policy

    def test_only_observations_exchanged(self):
        """Only observations and world updates are exchanged."""
        edge = EdgeNodeManager()
        cloud = CloudAggregator()
        sync = EdgeCloudSync(cloud, edge)

        node = EdgeNode(name="node-1")
        actor = EdgeActor("actor-1", node.node_id)
        node.actor = actor
        edge.register(node)

        actor.observe("s1", "s2")
        actor.learn("s1", "action_1", reward=0.8)

        sync.sync_edge_to_cloud(node.node_id)

        # Cloud has observations but NOT the policy
        assert cloud.world.nnz() == 1
        # Edge still has its own policy
        assert actor.policy.size == 1

    def test_policies_remain_local(self):
        """Policies, memories, rewards and strategies remain local."""
        edge = EdgeNodeManager()
        cloud = CloudAggregator()
        sync = EdgeCloudSync(cloud, edge)

        node = EdgeNode(name="node-1")
        actor = EdgeActor("actor-1", node.node_id)
        node.actor = actor
        edge.register(node)

        actor.learn("s1", "action_1", reward=0.8)
        sync.sync_edge_to_cloud(node.node_id)

        # Cloud does NOT have the policy
        assert cloud.world_learner.update_count == 0  # No Q-values in world
