"""Phase 11: Multi-Agent Collaboration - Integration Tests

Tests verify cooperative goal coordination, trust tracking, and capability discovery.
"""

import pytest
import asyncio
from datetime import datetime, timedelta

from src.actor.multi_agent_coordinator import (
    MultiAgentCoordinator,
    CollaborativeActor,
    SharedGoal,
    ActorCapability,
    TrustScore,
    CoordinationMessage,
    CoordinationMessageType,
)


class TestActorCapability:
    """Test actor capability declarations"""

    def test_create_capability(self):
        """Capability can be created"""
        cap = ActorCapability(
            capability_id="move",
            description="Can move to adjacent locations",
            success_rate=0.95,
            cost=10.0,
            duration_seconds=2.0,
        )

        assert cap.capability_id == "move"
        assert cap.success_rate == 0.95

    def test_capability_is_available(self):
        """Check if capability is available"""
        cap_good = ActorCapability(
            capability_id="move",
            description="Move",
            success_rate=0.8,
            cost=5.0,
            duration_seconds=1.0,
        )
        assert cap_good.is_available()

        cap_bad = ActorCapability(
            capability_id="broken",
            description="Broken",
            success_rate=0.3,
            cost=-10.0,
            duration_seconds=1.0,
        )
        assert not cap_bad.is_available()


class TestTrustScore:
    """Test trust tracking between actors"""

    def test_create_trust_score(self):
        """Trust score initialized at neutral"""
        trust = TrustScore(actor_a="actor1", actor_b="actor2")
        assert trust.score == 0.5
        assert trust.interactions == 0

    def test_update_on_success(self):
        """Trust increases on success"""
        trust = TrustScore(actor_a="actor1", actor_b="actor2")
        initial = trust.score

        trust.update_on_success()
        assert trust.score > initial
        assert trust.successful_collaborations == 1
        assert trust.interactions == 1

    def test_update_on_failure(self):
        """Trust decreases on failure"""
        trust = TrustScore(actor_a="actor1", actor_b="actor2", score=0.7)
        initial = trust.score

        trust.update_on_failure()
        assert trust.score < initial
        assert trust.failed_collaborations == 1

    def test_trust_thresholds(self):
        """Trust can be evaluated against thresholds"""
        trust = TrustScore(actor_a="actor1", actor_b="actor2", score=0.7)
        trust.interactions = 5

        assert trust.is_trustworthy(threshold=0.5)
        assert not trust.is_trustworthy(threshold=0.8)


class TestSharedGoal:
    """Test shared goal coordination"""

    def test_create_shared_goal(self):
        """Shared goal can be created"""
        goal = SharedGoal(
            goal_id="goal1",
            description="Hunt together",
            participants={"actor1", "actor2", "actor3"},
            leader_actor_id="actor1",
            required_capabilities=["track", "hunt", "communicate"],
        )

        assert goal.goal_id == "goal1"
        assert len(goal.participants) == 3
        assert goal.status == "proposed"

    def test_goal_acceptance_tracking(self):
        """Check if all participants accepted"""
        goal = SharedGoal(
            goal_id="goal1",
            description="Hunt",
            participants={"actor1", "actor2"},
            leader_actor_id="actor1",
        )

        assert not goal.all_accepted()

        goal.status = "accepted"
        assert goal.all_accepted()

    def test_goal_deadline(self):
        """Check goal deadlines"""
        future = datetime.now() + timedelta(hours=1)
        goal = SharedGoal(
            goal_id="goal1",
            description="Hunt",
            participants={"actor1"},
            leader_actor_id="actor1",
            deadline=future,
        )

        assert not goal.is_overdue()

        past = datetime.now() - timedelta(hours=1)
        goal.deadline = past
        assert goal.is_overdue()


class TestMultiAgentCoordinator:
    """Test coordination system"""

    def test_create_coordinator(self):
        """Coordinator can be created"""
        coordinator = MultiAgentCoordinator()
        assert coordinator is not None

    def test_register_actor(self):
        """Actor can register with coordinator"""
        coordinator = MultiAgentCoordinator()
        coordinator.register_actor("actor1")

        assert "actor1" in coordinator._actors_capabilities
        assert "actor1" in coordinator._message_queues

    def test_broadcast_capability(self):
        """Actor can broadcast capability"""
        coordinator = MultiAgentCoordinator()
        coordinator.register_actor("actor1")

        cap = ActorCapability(
            capability_id="move",
            description="Move",
            success_rate=0.9,
            cost=5.0,
            duration_seconds=1.0,
        )

        coordinator.broadcast_capability("actor1", cap)
        assert len(coordinator._actors_capabilities["actor1"]) == 1

    def test_discover_capabilities(self):
        """Discover actors with capabilities"""
        coordinator = MultiAgentCoordinator()

        # Register actors with capabilities
        for i in range(3):
            actor_id = f"actor{i}"
            coordinator.register_actor(actor_id)

            cap = ActorCapability(
                capability_id="move",
                description="Move",
                success_rate=0.9,
                cost=5.0,
                duration_seconds=1.0,
            )
            coordinator.broadcast_capability(actor_id, cap)

        # Discover
        results = coordinator.discover_capabilities("actor0", ["move"])

        assert "move" in results
        assert len(results["move"]) >= 2  # actor1 and actor2

    @pytest.mark.asyncio
    async def test_propose_shared_goal(self):
        """Propose shared goal to participants"""
        coordinator = MultiAgentCoordinator()

        for i in range(3):
            coordinator.register_actor(f"actor{i}")

        goal = SharedGoal(
            goal_id="goal1",
            description="Hunt together",
            participants={"actor1", "actor2", "actor0"},
            leader_actor_id="actor0",
        )

        success = await coordinator.propose_shared_goal(goal)
        assert success
        assert goal.goal_id in coordinator._shared_goals

    @pytest.mark.asyncio
    async def test_accept_shared_goal(self):
        """Actor accepts shared goal"""
        coordinator = MultiAgentCoordinator()

        for i in range(3):
            coordinator.register_actor(f"actor{i}")

        goal = SharedGoal(
            goal_id="goal1",
            description="Hunt",
            participants={"actor0", "actor1", "actor2"},
            leader_actor_id="actor0",
        )

        await coordinator.propose_shared_goal(goal)
        success = await coordinator.accept_shared_goal("actor1", "goal1")

        assert success

    @pytest.mark.asyncio
    async def test_reject_shared_goal(self):
        """Actor rejects shared goal"""
        coordinator = MultiAgentCoordinator()

        for i in range(3):
            coordinator.register_actor(f"actor{i}")

        goal = SharedGoal(
            goal_id="goal1",
            description="Hunt",
            participants={"actor0", "actor1", "actor2"},
            leader_actor_id="actor0",
        )

        await coordinator.propose_shared_goal(goal)
        success = await coordinator.reject_shared_goal("actor1", "goal1", "Too risky")

        assert success
        assert "actor1" not in goal.participants

    def test_trust_tracking(self):
        """Trust between actors is tracked"""
        coordinator = MultiAgentCoordinator()

        coordinator.update_trust("actor1", "actor2", success=True)
        trust = coordinator.get_trust("actor1", "actor2")

        assert trust is not None
        assert trust.successful_collaborations == 1

    def test_trusted_partners(self):
        """Get trusted partners for actor"""
        coordinator = MultiAgentCoordinator()

        # Build trust with actor2
        for _ in range(5):
            coordinator.update_trust("actor1", "actor2", success=True)

        # Low trust with actor3
        coordinator.update_trust("actor1", "actor3", success=False)

        trusted = coordinator.get_trusted_partners("actor1")
        assert "actor2" in trusted
        assert "actor3" not in trusted

    @pytest.mark.asyncio
    async def test_message_routing(self):
        """Messages route between actors"""
        coordinator = MultiAgentCoordinator()

        for i in range(3):
            coordinator.register_actor(f"actor{i}")

        message = CoordinationMessage(
            message_type=CoordinationMessageType.CAPABILITY_QUERY,
            sender_actor_id="actor0",
            recipient_actor_ids=["actor1"],
            payload={"capabilities_needed": ["move", "hunt"]},
        )

        await coordinator._send_message(message)

        # Recipient should receive message
        received = await coordinator.receive_message("actor1")
        assert received is not None
        assert received.message_type == CoordinationMessageType.CAPABILITY_QUERY

    @pytest.mark.asyncio
    async def test_broadcast_message(self):
        """Messages broadcast to multiple recipients"""
        coordinator = MultiAgentCoordinator()

        for i in range(3):
            coordinator.register_actor(f"actor{i}")

        message = CoordinationMessage(
            message_type=CoordinationMessageType.CAPABILITY_BROADCAST,
            sender_actor_id="actor0",
            recipient_actor_ids=["actor1", "actor2"],
            payload={"capability": "move"},
        )

        await coordinator._broadcast_message(message)

        # Both recipients should receive
        msg1 = await coordinator.receive_message("actor1")
        msg2 = await coordinator.receive_message("actor2")

        assert msg1 is not None
        assert msg2 is not None

    def test_coordinator_stats(self):
        """Coordinator provides statistics"""
        coordinator = MultiAgentCoordinator()

        for i in range(3):
            coordinator.register_actor(f"actor{i}")

        cap = ActorCapability(
            capability_id="move",
            description="Move",
            success_rate=0.9,
            cost=5.0,
            duration_seconds=1.0,
        )
        coordinator.broadcast_capability("actor0", cap)

        stats = coordinator.get_stats()

        assert stats["registered_actors"] == 3
        assert stats["total_capabilities_broadcast"] >= 1


class TestCollaborativeActor:
    """Test collaborative capabilities"""

    def test_create_collaborative_actor(self):
        """Collaborative actor can be created"""
        actor = CollaborativeActor()
        actor.id = "actor1"
        assert actor is not None

    def test_set_coordinator(self):
        """Actor can link to coordinator"""
        actor = CollaborativeActor()
        actor.id = "actor1"
        coordinator = MultiAgentCoordinator()

        actor.set_coordinator(coordinator)
        assert actor._coordinator is coordinator

    @pytest.mark.asyncio
    async def test_broadcast_capability(self):
        """Actor broadcasts its capabilities"""
        actor = CollaborativeActor()
        actor.id = "actor1"
        coordinator = MultiAgentCoordinator()

        actor.set_coordinator(coordinator)

        cap = ActorCapability(
            capability_id="move",
            description="Move",
            success_rate=0.9,
            cost=5.0,
            duration_seconds=1.0,
        )

        await actor.broadcast_capability(cap)
        assert len(actor._my_capabilities) == 1

    @pytest.mark.asyncio
    async def test_discover_collaborators(self):
        """Discover actors with needed capabilities"""
        coordinator = MultiAgentCoordinator()

        actor1 = CollaborativeActor()
        actor1.id = "actor1"
        actor1.set_coordinator(coordinator)

        actor2 = CollaborativeActor()
        actor2.id = "actor2"
        actor2.set_coordinator(coordinator)

        # Actor2 broadcasts capabilities
        cap = ActorCapability(
            capability_id="track",
            description="Track animals",
            success_rate=0.95,
            cost=10.0,
            duration_seconds=3.0,
        )
        await actor2.broadcast_capability(cap)

        # Actor1 discovers
        results = await actor1.discover_collaborators(["track"])

        assert "track" in results
        assert len(results["track"]) > 0

    @pytest.mark.asyncio
    async def test_propose_collaboration(self):
        """Actor proposes collaboration"""
        coordinator = MultiAgentCoordinator()

        actor1 = CollaborativeActor()
        actor1.id = "actor1"
        actor1.set_coordinator(coordinator)

        actor2 = CollaborativeActor()
        actor2.id = "actor2"
        actor2.set_coordinator(coordinator)

        goal = await actor1.propose_collaboration(
            goal_description="Hunt together",
            collaborator_ids=["actor2"],
            required_capabilities=["track", "hunt"],
        )

        assert goal is not None
        assert goal.leader_actor_id == "actor1"

    @pytest.mark.asyncio
    async def test_collaboration_acceptance(self):
        """Actor accepts collaboration"""
        coordinator = MultiAgentCoordinator()

        actor1 = CollaborativeActor()
        actor1.id = "actor1"
        actor1.set_coordinator(coordinator)

        actor2 = CollaborativeActor()
        actor2.id = "actor2"
        actor2.set_coordinator(coordinator)

        # Actor1 proposes
        goal = await actor1.propose_collaboration(
            goal_description="Hunt",
            collaborator_ids=["actor2"],
            required_capabilities=["hunt"],
        )

        # Actor2 accepts
        success = await actor2.accept_collaboration(goal.goal_id)
        assert success

    @pytest.mark.asyncio
    async def test_trust_after_collaboration(self):
        """Trust updates after collaboration"""
        coordinator = MultiAgentCoordinator()

        actor1 = CollaborativeActor()
        actor1.id = "actor1"
        actor1.set_coordinator(coordinator)

        actor2 = CollaborativeActor()
        actor2.id = "actor2"
        actor2.set_coordinator(coordinator)

        goal = await actor1.propose_collaboration(
            goal_description="Hunt",
            collaborator_ids=["actor2"],
            required_capabilities=[],
        )

        # Report success
        await actor1.report_collaboration_success(goal.goal_id, ["actor2"])

        trust = coordinator.get_trust("actor1", "actor2")
        assert trust.successful_collaborations == 1

    def test_collaboration_stats(self):
        """Actor provides collaboration stats"""
        actor = CollaborativeActor()
        actor.id = "actor1"

        stats = actor.get_collaboration_stats()
        assert "actor_id" in stats
        assert "capabilities" in stats


class TestMultiAgentIntegration:
    """Full integration tests for multi-agent scenarios"""

    @pytest.mark.asyncio
    async def test_three_actor_hunt_coordination(self):
        """Three actors coordinate on hunt"""
        coordinator = MultiAgentCoordinator()

        # Create actors
        actors = []
        for i in range(3):
            actor = CollaborativeActor()
            actor.id = f"actor{i}"
            actor.set_coordinator(coordinator)
            actors.append(actor)

        # Each broadcasts capability
        capabilities = ["track", "hunt", "communicate"]
        for i, actor in enumerate(actors):
            cap = ActorCapability(
                capability_id=capabilities[i],
                description=f"Can {capabilities[i]}",
                success_rate=0.9,
                cost=10.0 * (i + 1),
                duration_seconds=float(i + 1),
            )
            await actor.broadcast_capability(cap)

        # Actor0 proposes hunt
        goal = await actors[0].propose_collaboration(
            goal_description="Hunt together",
            collaborator_ids=[actors[1].id, actors[2].id],
            required_capabilities=["track", "hunt"],
        )

        assert goal is not None
        assert len(goal.participants) == 3

        # Actors accept
        for actor in actors[1:]:
            await actor.accept_collaboration(goal.goal_id)

        # Execute and report success
        await coordinator.start_shared_goal_execution(goal.goal_id)
        await actors[0].report_collaboration_success(goal.goal_id, [a.id for a in actors[1:]])

        # Check trust improved
        for i in range(1, 3):
            trust = coordinator.get_trust(actors[0].id, actors[i].id)
            assert trust.successful_collaborations > 0
