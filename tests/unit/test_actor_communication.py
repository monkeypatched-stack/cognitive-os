"""Simple test cases for actor-to-actor communication in a world."""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
from src.monkey_brain.kernel.compile.society import Actor, ActorNetwork


class TestBasicActorCommunication:
    """Core actor-to-actor communication mechanics."""

    def test_two_actors_share_world(self):
        """Two actors read from the same shared world."""
        world = SparseTransitionTensor()
        world.observe("s1", "s2")
        world.observe("s2", "s3")

        network = ActorNetwork(world)
        a = network.add("alice")
        b = network.add("bob")

        # Both discover transitions
        network.ask("alice", "s1")
        network.ask("bob", "s1")

        # Each discovers direct successors of s1
        assert a.world.nnz() >= 1
        assert b.world.nnz() >= 1

    def test_gossip_shares_knowledge(self):
        """Actor A asks, connected Actor B receives gossip."""
        from src.monkey_brain.kernel.compile.trust import Relationship

        world = SparseTransitionTensor()
        world.observe("s1", "s2")
        world.observe("s2", "s3")

        network = ActorNetwork(world)
        a = network.add("alice")
        b = network.add("bob")
        network.connect("alice", "bob", Relationship.COMMUNITY)

        network.ask("alice", "s1")

        # B received gossip
        assert b.world.nnz() >= 1

    def test_unconnected_actor_gets_nothing(self):
        """An unconnected actor receives no gossip."""
        world = SparseTransitionTensor()
        world.observe("s1", "s2")

        network = ActorNetwork(world)
        a = network.add("alice")
        b = network.add("bob")  # Not connected

        network.ask("alice", "s1")

        assert b.world.nnz() == 0

    def test_gossip_does_not_modify_world(self):
        """Gossip only modifies local beliefs, not the global world."""
        from src.monkey_brain.kernel.compile.trust import Relationship

        world = SparseTransitionTensor()
        world.observe("s1", "s2")
        world.observe("s2", "s3")
        initial = world.nnz()

        network = ActorNetwork(world)
        a = network.add("alice")
        b = network.add("bob")
        network.connect("alice", "bob", Relationship.COMMUNITY)

        network.ask("alice", "s1")

        assert world.nnz() == initial

    def test_different_beliefs_from_same_world(self):
        """Two actors can have different beliefs about the same world."""
        world = SparseTransitionTensor()
        world.observe("s1", "s2")
        world.observe("s1", "s3")
        world.observe("s2", "s4")
        world.observe("s3", "s5")

        network = ActorNetwork(world)
        a = network.add("alice")
        b = network.add("bob")

        # Alice asks about s1 → discovers s1→s2 and s1→s3
        network.ask("alice", "s1")

        # Bob asks about s2 → discovers s2→s4 only
        network.ask("bob", "s2")

        # Different beliefs about what they know
        assert a.world.nnz() != b.world.nnz()

    def test_policies_are_isolated(self):
        """Each actor has its own private policy."""
        a = Actor("alice")
        b = Actor("bob")

        a.policy.update("s1", "action_1", reward=0.9)
        b.policy.update("s1", "action_2", reward=0.3)

        # Alice knows action_1, not action_2
        assert a.policy.value("s1", "action_1") > 0.5
        assert a.policy.value("s1", "action_2") == 0.5

        # Bob knows action_2, not action_1
        assert b.policy.value("s1", "action_2") < 0.5
        assert b.policy.value("s1", "action_1") == 0.5

    def test_coverage_increases_with_asking(self):
        """Asking about states increases coverage of the world."""
        world = SparseTransitionTensor()
        for i in range(5):
            world.observe(f"s{i}", f"s{i + 1}")

        network = ActorNetwork(world)
        a = network.add("alice")

        before = network.coverage("alice")
        network.ask("alice", "s0")
        network.ask("alice", "s1")
        after = network.coverage("alice")

        assert after > before

    def test_full_cognitive_cycle(self):
        """Complete cycle: ask → observe → learn."""
        world = SparseTransitionTensor()
        world.observe("start", "middle")
        world.observe("middle", "goal")

        a = Actor("alice")
        result = a.cognitive_cycle("start", "goal", world)

        assert result.plan is not None
        assert result.observed is not None
        assert 0.0 <= result.epistemic_loss <= 1.0
