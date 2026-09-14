"""Tests for Step 12 — Society Subsystem (12.1-12.12).

Comprehensive tests covering the full society architecture: domain model,
shared world, observation, belief, interaction, coordination, runtime,
context stream, collective learning, governance, observability, and
planetary integration.
"""

from __future__ import annotations

import asyncio
import pytest
from dataclasses import FrozenInstanceError

from src.monkey_brain.kernel.society.domain import (
    Society,
    ActorIdentity,
    ActorProfile,
    ActorRole,
    ActorCapability,
    ActorRelationship,
    ActorMembership,
    ActorAddress,
    ActorStatus,
    ActorType,
    RelationshipType,
    CapabilityLevel,
)
from src.monkey_brain.kernel.society.world import (
    SharedWorld,
    WorldEntity,
    WorldRelationship,
    WorldEvent,
    WorldResource,
    WorldCapability,
    WorldLocation,
    WorldPolicy,
    WorldEntityType,
    RelationshipKind,
    EventType,
)
from src.monkey_brain.kernel.society.observation import (
    ObservationProvider,
    ObservationFilter,
    ActorObservation,
    ObservedEntity,
    ObservationQuality,
)
from src.monkey_brain.kernel.society.belief import (
    BeliefFusion,
    BeliefState,
    BeliefEntry,
    BeliefHypothesis,
)
from src.monkey_brain.kernel.society.interaction import (
    InteractionManager,
    Interaction,
    InteractionMessage,
    InteractionType,
    InteractionStatus,
)
from src.monkey_brain.kernel.society.coordination import (
    CoordinationEngine,
    Negotiation,
    NegotiationType,
    NegotiationStatus,
    ResourceAllocation,
    TaskAssignment,
)
from src.monkey_brain.kernel.society.runtime import (
    SocietyRuntime,
    ActorRuntimeState,
    SocietyTickResult,
)
from src.monkey_brain.kernel.society.context_stream import (
    SocietyContextStream,
    ContextEvent,
    ContextEventType,
    ContextSnapshot,
)
from src.monkey_brain.kernel.society.learning import (
    CollectiveLearningEngine,
    SharedExperience,
    LearningType,
    ReputationEntry,
    CollectiveLearningResult,
)
from src.monkey_brain.kernel.society.governance import (
    SocietyGovernanceEngine,
    GovernancePolicy,
    Permission,
    AuditEntry,
    TrustRecord,
    SafetyConstraint,
    GovernanceLevel,
    PolicyType,
    ComplianceStatus,
)
from src.monkey_brain.kernel.society.observability import (
    SocietyObservability,
    SocietyTrace,
    ActorTimeline,
    InteractionGraph,
    WorldEvolution,
    CoordinationMetrics,
    GovernanceReport,
)
from src.monkey_brain.kernel.society.integration import (
    PlanetaryRuntime,
    PlanetaryCycleResult,
    FederatedCycleResult,
)

# ═══════════════════════════════════════════════════════════════════════════
# Step 12.1 — Society Domain Model
# ═══════════════════════════════════════════════════════════════════════════


class TestDomainModel:
    def test_actor_identity_construction(self):
        identity = ActorIdentity(name="Alice", actor_type=ActorType.HUMAN)
        assert identity.name == "Alice"
        assert identity.actor_type == ActorType.HUMAN
        assert identity.actor_id

    def test_actor_profile_construction(self):
        cap = ActorCapability(name="driving", level=CapabilityLevel.EXPERT)
        profile = ActorProfile(
            identity=ActorIdentity(name="Bob", actor_type=ActorType.ROBOT),
            capabilities=(cap,),
            goals=("deliver_packages",),
            trust_level=0.8,
        )
        assert profile.identity.name == "Bob"
        assert len(profile.capabilities) == 1
        assert profile.trust_level == 0.8

    def test_society_construction(self):
        membership = ActorMembership(
            actor_id="a1",
            society_id="s1",
            role=ActorRole(name="worker"),
        )
        society = Society(name="Test Society", memberships=(membership,))
        assert society.name == "Test Society"
        assert "a1" in society.actor_ids()

    def test_society_members_with_role(self):
        m1 = ActorMembership(actor_id="a1", role=ActorRole(name="worker"))
        m2 = ActorMembership(actor_id="a2", role=ActorRole(name="manager"))
        society = Society(name="S", memberships=(m1, m2))
        workers = society.members_with_role("worker")
        assert len(workers) == 1
        assert workers[0].actor_id == "a1"

    def test_frozen(self):
        with pytest.raises(FrozenInstanceError):
            ActorIdentity().name = "x"
        with pytest.raises(FrozenInstanceError):
            Society().name = "x"

    def test_all_actor_types(self):
        for actor_type in ActorType:
            identity = ActorIdentity(actor_type=actor_type)
            assert identity.actor_type == actor_type


# ═══════════════════════════════════════════════════════════════════════════
# Step 12.2 — Shared Semantic World Model
# ═══════════════════════════════════════════════════════════════════════════


class TestSharedWorld:
    def test_add_and_get_entity(self):
        world = SharedWorld()
        entity = WorldEntity(name="Store A", entity_type=WorldEntityType.ENTITY)
        world.add_entity(entity)
        assert world.get_entity(entity.entity_id) is not None
        assert world.get_entity("nonexistent") is None

    def test_update_entity(self):
        world = SharedWorld()
        entity = WorldEntity(name="Store A", attributes={"stock": 10})
        world.add_entity(entity)
        updated = world.update_entity(entity.entity_id, stock=5)
        assert updated is not None
        assert updated.attributes["stock"] == 5
        assert updated.version == 1

    def test_remove_entity(self):
        world = SharedWorld()
        entity = WorldEntity(name="Temp")
        world.add_entity(entity)
        assert world.remove_entity(entity.entity_id) is True
        assert world.get_entity(entity.entity_id) is None
        assert world.remove_entity("nonexistent") is False

    def test_entities_by_type(self):
        world = SharedWorld()
        world.add_entity(WorldEntity(name="S1", entity_type=WorldEntityType.RESOURCE))
        world.add_entity(WorldEntity(name="S2", entity_type=WorldEntityType.ENTITY))
        resources = world.entities_by_type(WorldEntityType.RESOURCE)
        assert len(resources) == 1

    def test_relationships(self):
        world = SharedWorld()
        e1 = WorldEntity(name="A")
        e2 = WorldEntity(name="B")
        world.add_entity(e1)
        world.add_entity(e2)
        rel = WorldRelationship(source_id=e1.entity_id, target_id=e2.entity_id, kind=RelationshipKind.HAS)
        world.add_relationship(rel)
        rels = world.relationships_for(e1.entity_id)
        assert len(rels) == 1

    def test_events(self):
        world = SharedWorld()
        event = WorldEvent(event_type=EventType.OBSERVATION, description="test")
        world.record_event(event)
        events = world.events()
        assert len(events) == 1

    def test_versioning(self):
        world = SharedWorld()
        assert world.version == 0
        world.add_entity(WorldEntity(name="X"))
        assert world.version == 1
        world.add_entity(WorldEntity(name="Y"))
        assert world.version == 2

    def test_query(self):
        world = SharedWorld()
        world.add_entity(WorldEntity(name="Store A", entity_type=WorldEntityType.ENTITY))
        world.add_entity(WorldEntity(name="Store B", entity_type=WorldEntityType.RESOURCE))
        results = world.query(name_contains="Store")
        assert len(results) == 2
        results = world.query(entity_type=WorldEntityType.RESOURCE)
        assert len(results) == 1

    def test_resources_capabilities_locations_policies(self):
        world = SharedWorld()
        world.add_resource(WorldResource(name="Milk"))
        world.add_capability(WorldCapability(name="Delivery"))
        world.add_location(WorldLocation(name="Warehouse"))
        world.add_policy(WorldPolicy(name="No late deliveries"))
        assert len(world.resources()) == 1
        assert len(world.capabilities()) == 1
        assert len(world.locations()) == 1
        assert len(world.policies()) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Step 12.3 — Multi-Actor Observation
# ═══════════════════════════════════════════════════════════════════════════


class TestObservation:
    def test_observe_full_world(self):
        world = SharedWorld()
        world.add_entity(WorldEntity(name="Store A"))
        provider = ObservationProvider(world)
        obs = provider.observe("actor_1")
        assert len(obs.entities) == 1
        assert obs.actor_id == "actor_1"

    def test_partial_observation(self):
        world = SharedWorld()
        world.add_entity(WorldEntity(name="Store A"))
        world.add_entity(WorldEntity(name="Store B"))
        filt = ObservationFilter(partial_coverage=0.5)
        provider = ObservationProvider(world)
        obs = provider.observe("actor_1", filt)
        assert obs.quality == ObservationQuality.PARTIAL

    def test_noisy_observation(self):
        world = SharedWorld()
        world.add_entity(WorldEntity(name="Store A"))
        filt = ObservationFilter(noise_level=0.8)
        provider = ObservationProvider(world)
        obs = provider.observe("actor_1", filt)
        assert obs.quality == ObservationQuality.NOISY

    def test_observe_specific_entity(self):
        world = SharedWorld()
        e = WorldEntity(name="Store A")
        world.add_entity(e)
        provider = ObservationProvider(world)
        obs = provider.observe_entity("actor_1", e.entity_id)
        assert obs is not None
        assert obs.entity.name == "Store A"

    def test_observe_nonexistent_entity(self):
        world = SharedWorld()
        provider = ObservationProvider(world)
        obs = provider.observe_entity("actor_1", "nonexistent")
        assert obs is None

    def test_observation_includes_events(self):
        world = SharedWorld()
        world.record_event(WorldEvent(description="test event"))
        provider = ObservationProvider(world)
        obs = provider.observe("actor_1")
        assert len(obs.events) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Step 12.4 — Distributed Belief Formation
# ═══════════════════════════════════════════════════════════════════════════


class TestBeliefFormation:
    def test_fuse_creates_beliefs(self):
        world = SharedWorld()
        world.add_entity(WorldEntity(name="Store A", confidence=0.9))
        provider = ObservationProvider(world)
        obs = provider.observe("actor_1")
        fusion = BeliefFusion()
        state = fusion.fuse("actor_1", obs)
        assert len(state.beliefs) >= 1
        assert state.actor_id == "actor_1"

    def test_fuse_updates_existing_beliefs(self):
        world = SharedWorld()
        world.add_entity(WorldEntity(name="Store A", confidence=0.9))
        provider = ObservationProvider(world)
        obs = provider.observe("actor_1")
        fusion = BeliefFusion()
        state1 = fusion.fuse("actor_1", obs)
        state2 = fusion.fuse("actor_1", obs, state1)
        assert state2.version > state1.version

    def test_belief_confidence(self):
        entry = BeliefEntry(
            subject="Store A",
            hypotheses=(BeliefHypothesis(confidence=0.9),),
        )
        assert entry.confidence == 0.9

    def test_uncertain_beliefs(self):
        state = BeliefState(
            beliefs=(
                BeliefEntry(subject="A", hypotheses=(BeliefHypothesis(confidence=0.9),)),
                BeliefEntry(subject="B", hypotheses=(BeliefHypothesis(confidence=0.3),)),
            )
        )
        uncertain = state.uncertain_beliefs(0.5)
        assert len(uncertain) == 1
        assert uncertain[0].subject == "B"

    def test_belief_isolation(self):
        fusion = BeliefFusion()
        world = SharedWorld()
        world.add_entity(WorldEntity(name="X"))
        provider = ObservationProvider(world)
        obs = provider.observe("actor_1")
        state1 = fusion.fuse("actor_1", obs)
        state2 = fusion.fuse("actor_2", obs)
        assert state1.actor_id != state2.actor_id
        assert state1.beliefs != state2.beliefs or state1.actor_id != state2.actor_id


# ═══════════════════════════════════════════════════════════════════════════
# Step 12.5 — Social Interaction Framework
# ═══════════════════════════════════════════════════════════════════════════


class TestInteraction:
    def test_create_interaction(self):
        mgr = InteractionManager()
        interaction = mgr.create_interaction(
            InteractionType.REQUEST,
            "alice",
            ("bob",),
            topic="milk delivery",
        )
        assert interaction.interaction_type == InteractionType.REQUEST
        assert interaction.initiator_id == "alice"

    def test_send_message(self):
        mgr = InteractionManager()
        interaction = mgr.create_interaction(
            InteractionType.INFORM,
            "alice",
            ("bob",),
        )
        msg = InteractionMessage(
            sender_id="alice",
            receiver_id="bob",
            content="Store is open",
        )
        updated = mgr.send_message(interaction.interaction_id, msg)
        assert updated is not None
        assert len(updated.messages) == 1

    def test_respond(self):
        mgr = InteractionManager()
        interaction = mgr.create_interaction(
            InteractionType.REQUEST,
            "alice",
            ("bob",),
        )
        updated = mgr.respond(interaction.interaction_id, "bob", True)
        assert updated is not None
        assert updated.status == InteractionStatus.ACCEPTED

    def test_reject(self):
        mgr = InteractionManager()
        interaction = mgr.create_interaction(
            InteractionType.REQUEST,
            "alice",
            ("bob",),
        )
        updated = mgr.respond(interaction.interaction_id, "bob", False)
        assert updated.status == InteractionStatus.REJECTED

    def test_interactions_for_actor(self):
        mgr = InteractionManager()
        mgr.create_interaction(InteractionType.INFORM, "alice", ("bob",))
        mgr.create_interaction(InteractionType.INFORM, "charlie", ("alice",))
        alice_interactions = mgr.interactions_for("alice")
        assert len(alice_interactions) == 2

    def test_all_interaction_types(self):
        for itype in InteractionType:
            mgr = InteractionManager()
            interaction = mgr.create_interaction(itype, "a", ("b",))
            assert interaction.interaction_type == itype


# ═══════════════════════════════════════════════════════════════════════════
# Step 12.6 — Coordination & Negotiation
# ═══════════════════════════════════════════════════════════════════════════


class TestCoordination:
    def test_propose_negotiation(self):
        engine = CoordinationEngine()
        neg = engine.propose(
            NegotiationType.BILATERAL,
            "alice",
            ("bob",),
            topic="resource allocation",
            proposal_content={"milk": 2},
        )
        assert neg.negotiation_type == NegotiationType.BILATERAL
        assert neg.status == NegotiationStatus.PROPOSED

    def test_counter_propose(self):
        engine = CoordinationEngine()
        neg = engine.propose(
            NegotiationType.BILATERAL,
            "alice",
            ("bob",),
            topic="resources",
            proposal_content={"milk": 2},
        )
        updated = engine.counter_propose(neg.negotiation_id, "bob", {"milk": 1})
        assert updated is not None
        assert updated.status == NegotiationStatus.COUNTER_PROPOSED

    def test_accept_negotiation(self):
        engine = CoordinationEngine()
        neg = engine.propose(NegotiationType.BILATERAL, "a", ("b",), topic="x", proposal_content="y")
        updated = engine.accept(neg.negotiation_id)
        assert updated.status == NegotiationStatus.ACCEPTED

    def test_resource_allocation(self):
        engine = CoordinationEngine()
        alloc = engine.allocate_resources("res1", {"alice": 5, "bob": 3})
        assert alloc.allocations["alice"] == 5

    def test_task_assignment(self):
        engine = CoordinationEngine()
        task = engine.assign_task("t1", "deliver milk", "robot_1", "alice")
        assert task.assigned_to == "robot_1"

    def test_conflict_resolution(self):
        engine = CoordinationEngine()
        resolution = engine.resolve_conflict(("alice", "bob"), "split evenly")
        assert resolution.resolution == "split evenly"


# ═══════════════════════════════════════════════════════════════════════════
# Step 12.7 — Society Runtime
# ═══════════════════════════════════════════════════════════════════════════


class TestSocietyRuntime:
    def test_register_actor(self):
        rt = SocietyRuntime()
        profile = ActorProfile(identity=ActorIdentity(name="Alice"))
        state = rt.register_actor(profile)
        assert state.actor_id == profile.identity.actor_id
        assert len(rt.all_actors()) == 1

    def test_register_actor_without_actor_arg_is_never_none(self):
        """Step 13.6: no ghost actors — register_actor(profile) with no
        actor= supplied must auto-construct a real, tickable CognitiveActor
        instead of leaving ActorRuntimeState.actor permanently None."""
        from src.monkey_brain.kernel.compile.cognitive_actor import CognitiveActor

        rt = SocietyRuntime()
        profile = ActorProfile(identity=ActorIdentity(name="Alice"))
        state = rt.register_actor(profile)
        assert state.actor is not None
        assert isinstance(state.actor, CognitiveActor)
        assert state.status == ActorStatus.REGISTERED
        # still immediately schedulable — unchanged from pre-13.6 behavior
        assert len(rt.active_actors()) == 1

    def test_register_actor_shares_actor_belief_tensor(self):
        """Step 14.5: register_actor()'s own ActorRuntime construction must
        pass local_belief=actor.belief — without it, ActorRuntime.__init__
        creates a brand-new empty SparseTransitionTensor and silently
        reassigns actor.belief to it (via the .world property setter),
        detaching the tensor from whatever the actor already had. This
        regression test locks in that the registered ActorRuntime shares
        the SAME tensor object the actor itself holds, immediately after
        registration."""
        rt = SocietyRuntime()
        profile = ActorProfile(identity=ActorIdentity(name="Alice"))
        state = rt.register_actor(profile)

        assert state.actor_runtime.belief is state.actor.belief

    def test_register_actor_rejects_malformed_actor(self):
        """Step 13.6: a caller-supplied actor= that doesn't satisfy
        ActorProtocol fails fast instead of being silently accepted and
        never ticking."""

        class NotAnActor:
            pass

        rt = SocietyRuntime()
        profile = ActorProfile(identity=ActorIdentity(name="Bob"))
        with pytest.raises(TypeError):
            rt.register_actor(profile, actor=NotAnActor())

    def test_unregister_actor(self):
        rt = SocietyRuntime()
        profile = ActorProfile(identity=ActorIdentity(name="Alice"))
        rt.register_actor(profile)
        assert rt.unregister_actor(profile.identity.actor_id) is True
        assert len(rt.all_actors()) == 0

    def test_activate_deactivate(self):
        rt = SocietyRuntime()
        profile = ActorProfile(identity=ActorIdentity(name="Alice"))
        rt.register_actor(profile)
        rt.deactivate_actor(profile.identity.actor_id)
        assert len(rt.active_actors()) == 0
        rt.activate_actor(profile.identity.actor_id)
        assert len(rt.active_actors()) == 1

    def test_world_operations(self):
        rt = SocietyRuntime()
        entity = WorldEntity(name="Store A")
        rt.world.add_entity(entity)
        assert rt.world.get_entity(entity.entity_id) is not None

    @pytest.mark.asyncio
    async def test_tick(self):
        rt = SocietyRuntime()
        profile = ActorProfile(identity=ActorIdentity(name="Alice"))
        rt.register_actor(profile)
        result = await rt.tick()
        assert isinstance(result, SocietyTickResult)
        assert result.actors_ticked == 1

    @pytest.mark.asyncio
    async def test_tick_transitions_registered_to_initialized(self):
        """Step 13.6: a freshly registered actor's status moves from
        REGISTERED to INITIALIZED on its first successful coordinated tick —
        descriptive lifecycle bookkeeping only, doesn't gate scheduling."""
        rt = SocietyRuntime()
        profile = ActorProfile(identity=ActorIdentity(name="Alice"))
        state = rt.register_actor(profile)
        assert state.status == ActorStatus.REGISTERED
        await rt.tick()
        assert rt.get_actor(profile.identity.actor_id).status == ActorStatus.INITIALIZED

    def test_to_dict(self):
        rt = SocietyRuntime()
        d = rt.to_dict()
        assert "actor_count" in d
        assert "world_version" in d


# ═══════════════════════════════════════════════════════════════════════════
# Step 12.8 — Context Stream
# ═══════════════════════════════════════════════════════════════════════════


class TestContextStream:
    def test_publish_and_read(self):
        stream = SocietyContextStream()
        event = ContextEvent(event_type=ContextEventType.OBSERVATION, description="test")
        stream.publish(event)
        assert stream.event_count == 1
        assert stream.version == 1

    def test_replay(self):
        stream = SocietyContextStream()
        stream.publish(ContextEvent(event_type=ContextEventType.OBSERVATION, actor_id="a1"))
        stream.publish(ContextEvent(event_type=ContextEventType.BELIEF_UPDATE, actor_id="a2"))
        stream.publish(ContextEvent(event_type=ContextEventType.OBSERVATION, actor_id="a1"))
        replayed = stream.replay(event_type=ContextEventType.OBSERVATION)
        assert len(replayed) == 2

    def test_replay_by_actor(self):
        stream = SocietyContextStream()
        stream.publish(ContextEvent(event_type=ContextEventType.OBSERVATION, actor_id="a1"))
        stream.publish(ContextEvent(event_type=ContextEventType.OBSERVATION, actor_id="a2"))
        replayed = stream.replay(actor_id="a1")
        assert len(replayed) == 1

    def test_subscribe(self):
        stream = SocietyContextStream()
        received = []
        stream.subscribe(lambda e: received.append(e))
        stream.publish(ContextEvent(event_type=ContextEventType.ACTION))
        assert len(received) == 1

    def test_snapshot(self):
        stream = SocietyContextStream()
        stream.publish(ContextEvent(event_type=ContextEventType.ACTION))
        snap = stream.snapshot()
        assert snap.event_count == 1
        assert snap.version == 1

    def test_audit_trail(self):
        stream = SocietyContextStream()
        event = ContextEvent(event_type=ContextEventType.ACTION, description="audit me")
        published = stream.publish(event)
        found = stream.audit_trail(published.event_id)
        assert found is not None

    def test_provenance(self):
        stream = SocietyContextStream()
        stream.publish(ContextEvent(event_type=ContextEventType.ACTION, actor_id="a1"))
        stream.publish(ContextEvent(event_type=ContextEventType.ACTION, actor_id="a2"))
        provenance = stream.provenance_for("a1")
        assert len(provenance) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Step 12.9 — Collective Learning
# ═══════════════════════════════════════════════════════════════════════════


class TestCollectiveLearning:
    def test_share_experience(self):
        engine = CollectiveLearningEngine()
        exp = SharedExperience(
            actor_id="robot_1",
            description="Delivered milk successfully",
            outcome="success",
            confidence=0.9,
            lessons=("route_optimization",),
        )
        result = engine.share_experience(exp)
        assert isinstance(result, CollectiveLearningResult)
        assert result.experience_id == exp.experience_id

    def test_reputation_updates(self):
        engine = CollectiveLearningEngine()
        for _ in range(5):
            engine.share_experience(
                SharedExperience(
                    actor_id="robot_1",
                    outcome="success",
                    confidence=0.9,
                )
            )
        rep = engine.get_reputation("robot_1")
        assert rep.score > 0.5
        assert rep.evidence_count == 5

    def test_reputation_decreases_on_failure(self):
        engine = CollectiveLearningEngine()
        engine.share_experience(
            SharedExperience(
                actor_id="robot_1",
                outcome="success",
                confidence=0.9,
            )
        )
        engine.share_experience(
            SharedExperience(
                actor_id="robot_1",
                outcome="failure",
                confidence=0.9,
            )
        )
        rep = engine.get_reputation("robot_1")
        assert rep.evidence_count == 2

    def test_experiences_filter(self):
        engine = CollectiveLearningEngine()
        engine.share_experience(SharedExperience(actor_id="a1", learning_type=LearningType.SHARED_EXPERIENCE))
        engine.share_experience(SharedExperience(actor_id="a2", learning_type=LearningType.CAPABILITY_IMPROVEMENT))
        exps = engine.experiences(actor_id="a1")
        assert len(exps) == 1

    def test_world_impact(self):
        engine = CollectiveLearningEngine()
        exp = SharedExperience(
            actor_id="a1",
            outcome="success",
            world_impact={"store_status": "open"},
        )
        result = engine.share_experience(exp)
        assert result.world_refinements.get("store_status") == "open"


# ═══════════════════════════════════════════════════════════════════════════
# Step 12.10 — Society Governance
# ═══════════════════════════════════════════════════════════════════════════


class TestGovernance:
    def test_add_policy(self):
        gov = SocietyGovernanceEngine()
        policy = GovernancePolicy(name="No unauthorized access", policy_type=PolicyType.PERMISSION)
        gov.add_policy(policy)
        assert len(gov.policies()) == 1

    def test_grant_and_check_permission(self):
        gov = SocietyGovernanceEngine()
        gov.grant_permission(Permission(actor_id="alice", resource="store", action="read"))
        assert gov.check_permission("alice", "store", "read") is True
        assert gov.check_permission("bob", "store", "read") is False

    def test_revoke_permission(self):
        gov = SocietyGovernanceEngine()
        gov.grant_permission(Permission(actor_id="alice", resource="store", action="read"))
        assert gov.revoke_permission("alice", "store", "read") is True
        assert gov.check_permission("alice", "store", "read") is False

    def test_trust_evaluation(self):
        gov = SocietyGovernanceEngine()
        record = gov.evaluate_trust("alice", 0.9, {"reliability": 0.95})
        assert record.trust_score == 0.9
        assert record.factors["reliability"] == 0.95

    def test_audit(self):
        gov = SocietyGovernanceEngine()
        entry = gov.audit("alice", "access_store", compliance_status=ComplianceStatus.COMPLIANT)
        assert entry.compliance_status == ComplianceStatus.COMPLIANT
        assert len(gov.audit_log()) == 1

    def test_compliance_check(self):
        gov = SocietyGovernanceEngine()
        gov.audit("alice", "action", compliance_status=ComplianceStatus.COMPLIANT)
        assert gov.check_compliance("alice") == ComplianceStatus.COMPLIANT
        assert gov.check_compliance("unknown") == ComplianceStatus.UNDER_REVIEW

    def test_safety_constraints(self):
        gov = SocietyGovernanceEngine()
        constraint = SafetyConstraint(name="No self-harm", rule="never harm self", applies_to=("robot",))
        gov.add_safety_constraint(constraint)
        violations = gov.check_safety("r1", "robot")
        assert len(violations) == 1

    def test_policies_filter(self):
        gov = SocietyGovernanceEngine()
        gov.add_policy(GovernancePolicy(name="P1", policy_type=PolicyType.PERMISSION))
        gov.add_policy(GovernancePolicy(name="P2", policy_type=PolicyType.SAFETY))
        perms = gov.policies(policy_type=PolicyType.PERMISSION)
        assert len(perms) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Step 12.11 — Society Observability
# ═══════════════════════════════════════════════════════════════════════════


class TestObservability:
    def test_build_actor_timelines(self):
        obs = SocietyObservability()
        events = (
            ContextEvent(
                event_type=ContextEventType.OBSERVATION,
                actor_id="a1",
                description="obs1",
            ),
            ContextEvent(
                event_type=ContextEventType.BELIEF_UPDATE,
                actor_id="a1",
                description="belief1",
            ),
            ContextEvent(
                event_type=ContextEventType.OBSERVATION,
                actor_id="a2",
                description="obs2",
            ),
        )
        timelines = obs.build_actor_timelines(events)
        assert len(timelines) == 2

    def test_build_interaction_graph(self):
        obs = SocietyObservability()
        events = (
            ContextEvent(
                event_type=ContextEventType.INTERACTION,
                actor_id="a1",
                description="interact",
                payload={"participants": ["a2"]},
            ),
        )
        graph = obs.build_interaction_graph(events)
        assert graph.total_interactions == 1
        assert len(graph.edges) == 1

    def test_build_society_trace(self):
        obs = SocietyObservability()
        events = (
            ContextEvent(
                event_type=ContextEventType.OBSERVATION,
                actor_id="a1",
                description="obs",
            ),
        )
        trace = obs.build_society_trace(events, total_ticks=5, active_actors=2)
        assert isinstance(trace, SocietyTrace)
        assert trace.narrative
        assert trace.to_dict()["actor_count"] == 1

    def test_narrative_contains_sections(self):
        obs = SocietyObservability()
        trace = obs.build_society_trace(())
        assert "Society Trace" in trace.narrative
        assert "Actors" in trace.narrative
        assert "Interactions" in trace.narrative


# ═══════════════════════════════════════════════════════════════════════════
# Step 12.12 — Planetary Runtime Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestPlanetaryRuntime:
    def setup_method(self):
        try:
            import redis as _redis

            r = _redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=1)
            r.flushdb()
        except Exception:
            pass

    def test_register_actor(self):
        pr = PlanetaryRuntime()
        profile = ActorProfile(identity=ActorIdentity(name="Alice", actor_type=ActorType.HUMAN))
        state = pr.register_actor(profile)
        assert len(pr.active_actors()) == 1

    def test_add_world_entity(self):
        pr = PlanetaryRuntime()
        entity = WorldEntity(name="Store A")
        pr.add_world_entity(entity)
        assert pr.world.get_entity(entity.entity_id) is not None

    def test_send_interaction(self):
        pr = PlanetaryRuntime()
        interaction = pr.send_interaction(
            InteractionType.REQUEST,
            "alice",
            ("bob",),
            topic="milk",
        )
        assert interaction.interaction_type == InteractionType.REQUEST

    def test_share_experience(self):
        pr = PlanetaryRuntime()
        exp = SharedExperience(actor_id="a1", outcome="success", description="delivered")
        result = pr.share_experience(exp)
        assert isinstance(result, CollectiveLearningResult)

    @pytest.mark.asyncio
    async def test_cycle(self):
        pr = PlanetaryRuntime()
        profile = ActorProfile(identity=ActorIdentity(name="Alice"))
        pr.register_actor(profile)
        result = await pr.cycle()
        assert isinstance(result, PlanetaryCycleResult)
        assert result.actors_observed == 1

    def test_trace(self):
        pr = PlanetaryRuntime()
        trace = pr.trace()
        assert isinstance(trace, SocietyTrace)

    def test_to_dict(self):
        pr = PlanetaryRuntime()
        d = pr.to_dict()
        assert "society_id" in d
        assert "actor_count" in d
        assert "world_version" in d

    def test_governance_integration(self):
        pr = PlanetaryRuntime()
        # check_permission is membership-aware (Governance/Membership/
        # Registration Model refactor): a grant only counts while the
        # actor has an active membership in the society that owns it, so
        # alice must actually be registered, not merely granted a
        # permission in the abstract.
        alice = pr.register_actor(ActorProfile(identity=ActorIdentity(name="alice", actor_type=ActorType.HUMAN)))
        pr.governance.grant_permission(Permission(actor_id=alice.actor_id, resource="store", action="read"))
        assert pr.check_permission(alice.actor_id, "store", "read") is True
        assert pr.check_permission("bob", "store", "read") is False

    @pytest.mark.asyncio
    async def test_full_acceptance_scenario(self):
        """Test the acceptance criteria: Human requests milk,
        Retail Enterprise checks inventory, Robot retrieves product."""
        pr = PlanetaryRuntime()

        human = ActorProfile(
            identity=ActorIdentity(name="Customer", actor_type=ActorType.HUMAN),
            goals=("get_milk",),
        )
        enterprise = ActorProfile(
            identity=ActorIdentity(name="RetailCo", actor_type=ActorType.ENTERPRISE),
            capabilities=(ActorCapability(name="inventory_check"),),
        )
        robot = ActorProfile(
            identity=ActorIdentity(name="RetrievalBot", actor_type=ActorType.ROBOT),
            capabilities=(ActorCapability(name="product_retrieval"),),
        )

        pr.register_actor(human)
        pr.register_actor(enterprise)
        pr.register_actor(robot)

        pr.add_world_entity(
            WorldEntity(
                name="Store A",
                entity_type=WorldEntityType.ENTITY,
                attributes={"milk_stock": 10},
            )
        )

        interaction1 = pr.send_interaction(
            InteractionType.REQUEST,
            human.identity.actor_id,
            (enterprise.identity.actor_id,),
            topic="milk_request",
            proposal={"item": "milk", "quantity": 2},
        )
        assert interaction1.interaction_type == InteractionType.REQUEST

        result = await pr.cycle()
        assert result.actors_observed == 3

        trace = pr.trace()
        assert trace.coordination_metrics.active_actors == 3

    # ── Society Activation/Deactivation ──────────────────────────────────

    def test_activate_society(self):
        pr = PlanetaryRuntime()
        sr = pr.create_society("Test Society")
        assert sr.is_active is True
        pr.deactivate_society(sr.society.society_id)
        assert sr.is_active is False
        pr.activate_society(sr.society.society_id)
        assert sr.is_active is True

    def test_deactivate_society(self):
        pr = PlanetaryRuntime()
        sr = pr.create_society("Test Society")
        assert sr.is_active is True
        result = pr.deactivate_society(sr.society.society_id)
        assert result is True
        assert sr.is_active is False

    def test_activate_nonexistent_society(self):
        pr = PlanetaryRuntime()
        result = pr.activate_society("nonexistent")
        assert result is False

    def test_deactivate_nonexistent_society(self):
        pr = PlanetaryRuntime()
        result = pr.deactivate_society("nonexistent")
        assert result is False

    def test_cycle_skips_inactive_societies(self):
        import asyncio

        async def _test():
            pr = PlanetaryRuntime()
            sr1 = pr.create_society("Active Society")
            sr2 = pr.create_society("Inactive Society")

            # Register actors in both societies
            profile1 = ActorProfile(identity=ActorIdentity(name="Alice"))
            sr1.register_actor(profile1)

            profile2 = ActorProfile(identity=ActorIdentity(name="Bob"))
            sr2.register_actor(profile2)

            # Deactivate the second society
            pr.deactivate_society(sr2.society.society_id)

            # Verify the society is deactivated
            assert sr2.is_active is False

            # Verify the other society is still active
            assert sr1.is_active is True

            # Verify activate/deactivate cycle works
            pr.activate_society(sr2.society.society_id)
            assert sr2.is_active is True

        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Item #9 — Multi-society federation
# ═══════════════════════════════════════════════════════════════════════════


class TestMultiSocietyRegistry:
    def setup_method(self):
        try:
            import redis as _redis

            r = _redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=1)
            r.flushdb()
        except Exception:
            pass

    def test_add_society(self):
        pr = PlanetaryRuntime()
        sr = SocietyRuntime(Society(name="Second Society"))
        pr.add_society(sr)
        assert pr.get_society_runtime(sr.society.society_id) is sr
        assert len(pr.all_societies()) == 2

    def test_create_society(self):
        pr = PlanetaryRuntime()
        sr = pr.create_society("New Society", "A test society")
        assert sr.society.name == "New Society"
        assert sr.society.description == "A test society"
        assert pr.get_society_runtime(sr.society.society_id) is sr
        assert len(pr.all_societies()) == 2

    def test_all_societies_includes_primary(self):
        pr = PlanetaryRuntime()
        assert len(pr.all_societies()) == 1
        assert pr.all_societies()[0] is pr._society_runtime

    def test_get_society_runtime_unknown_returns_none(self):
        pr = PlanetaryRuntime()
        assert pr.get_society_runtime("nonexistent") is None

    def test_to_dict_includes_society_registry_count(self):
        pr = PlanetaryRuntime()
        pr.create_society("S2")
        d = pr.to_dict()
        assert d["society_registry_count"] == 2


class TestFederatedCycle:
    def setup_method(self):
        try:
            import redis as _redis

            r = _redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=1)
            r.flushdb()
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_federated_cycle_ticks_all_members(self):
        pr = PlanetaryRuntime()
        sr2 = pr.create_society("Society B")
        # create_society() auto-hosts every new society at the same
        # bootstrap "Default City" pr's own default society is already
        # hosted at (see add_society's docstring) — an actor physically
        # placed there (pr.register_actor's geography placement) becomes a
        # TEMPORARY coordination participant of every society hosted at
        # that city (True Multi-Actor Coordination — see
        # PlanetaryRuntime._on_temporary_membership_granted), which would
        # make Alice count toward sr2's tick too. Rehost sr2 at its own
        # city so this test's two societies stay physically independent,
        # matching what it actually asserts (each actor ticked exactly once).
        country_b = pr.create_country("Country B")
        city_b = pr.create_city("City B", country_b.entity_id)
        pr.assign_society_to_city(sr2.society.society_id, city_b.entity_id)
        profile1 = ActorProfile(identity=ActorIdentity(name="Alice"))
        profile2 = ActorProfile(identity=ActorIdentity(name="Bob"))
        pr.register_actor(profile1)
        sr2.register_actor(profile2)
        federation = pr.create_federation(
            "TestFed",
            member_society_ids=(
                pr.society.society_id,
                sr2.society.society_id,
            ),
        )
        result = await pr.federated_cycle(federation.federation_id)
        assert isinstance(result, FederatedCycleResult)
        assert len(result.societies_ticked) == 2
        assert result.actors_ticked_total == 2
        assert len(result.unregistered_society_ids) == 0

    @pytest.mark.asyncio
    async def test_federated_cycle_skips_unregistered(self):
        pr = PlanetaryRuntime()
        federation = pr.create_federation(
            "Fed",
            member_society_ids=(
                pr.society.society_id,
                "remote_society_not_here",
            ),
        )
        result = await pr.federated_cycle(federation.federation_id)
        assert len(result.societies_ticked) == 1
        assert result.unregistered_society_ids == ("remote_society_not_here",)
        assert result.actors_ticked_total == 0

    @pytest.mark.asyncio
    async def test_federated_cycle_nonexistent_federation(self):
        pr = PlanetaryRuntime()
        result = await pr.federated_cycle("no_such_fed")
        assert result.federation_id == "no_such_fed"
        assert len(result.societies_ticked) == 0

    @pytest.mark.asyncio
    async def test_federated_cycle_records_cross_society_events(self):
        pr = PlanetaryRuntime()
        sr2 = pr.create_society("S2")
        profile = ActorProfile(identity=ActorIdentity(name="Eve"))
        sr2.register_actor(profile)
        federation = pr.create_federation(
            "Fed",
            member_society_ids=(
                pr.society.society_id,
                sr2.society.society_id,
            ),
        )
        await pr.federated_cycle(federation.federation_id)
        updated = pr.federation_manager.get_federation(federation.federation_id)
        assert len(updated.shared_context_history) > 0

    @pytest.mark.asyncio
    async def test_join_federation_and_cycle(self):
        pr = PlanetaryRuntime()
        sr2 = pr.create_society("S2")
        # See test_federated_cycle_ticks_all_members's comment: rehost sr2
        # off the shared "Default City" so A1's physical placement doesn't
        # make it a temporary participant of sr2 too.
        country_b = pr.create_country("Country B2")
        city_b = pr.create_city("City B2", country_b.entity_id)
        pr.assign_society_to_city(sr2.society.society_id, city_b.entity_id)
        profile1 = ActorProfile(identity=ActorIdentity(name="A1"))
        profile2 = ActorProfile(identity=ActorIdentity(name="A2"))
        pr.register_actor(profile1)
        sr2.register_actor(profile2)
        federation = pr.create_federation("Fed")
        pr.join_federation(federation.federation_id)
        pr.federation_manager.add_member(federation.federation_id, sr2.society.society_id)
        federation = pr.federation_manager.get_federation(federation.federation_id)
        assert pr.society.society_id in federation.member_society_ids
        assert sr2.society.society_id in federation.member_society_ids
        result = await pr.federated_cycle(federation.federation_id)
        assert len(result.societies_ticked) == 2
        assert result.actors_ticked_total == 2
        assert len(result.unregistered_society_ids) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary — society model independent of networking/messaging
# ═══════════════════════════════════════════════════════════════════════════


def _imported_modules(mod) -> list[str]:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(mod))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


class TestOwnershipBoundary:
    def test_domain_independent_of_networking(self):
        import src.monkey_brain.kernel.society.domain as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "socket",
            "http",
            "grpc",
            "asyncio",
            "aiohttp",
            "fastapi",
            "flask",
        ):
            assert forbidden not in imports, f"domain.py must not import: {forbidden}"

    def test_domain_independent_of_persistence(self):
        import src.monkey_brain.kernel.society.domain as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "sqlite",
            "postgres",
            "mongo",
            "redis",
            "sqlalchemy",
            "dynamodb",
        ):
            assert forbidden not in imports, f"domain.py must not import: {forbidden}"


# ═══════════════════════════════════════════════════════════════════════════
# Serialization round-trip tests
# ═══════════════════════════════════════════════════════════════════════════


class TestDomainSerialization:
    def test_actor_identity_roundtrip(self):
        original = ActorIdentity(name="Alice", actor_type=ActorType.ROBOT, description="Test bot")
        d = original.to_dict()
        restored = ActorIdentity.from_dict(d)
        assert restored.actor_id == original.actor_id
        assert restored.name == "Alice"
        assert restored.actor_type == ActorType.ROBOT
        assert restored.description == "Test bot"

    def test_actor_capability_roundtrip(self):
        original = ActorCapability(name="driving", level=CapabilityLevel.EXPERT)
        d = original.to_dict()
        restored = ActorCapability.from_dict(d)
        assert restored.name == "driving"
        assert restored.level == CapabilityLevel.EXPERT

    def test_actor_profile_roundtrip(self):
        original = ActorProfile(
            identity=ActorIdentity(name="Bob", actor_type=ActorType.ENTERPRISE),
            capabilities=(ActorCapability(name="inventory", level=CapabilityLevel.MASTER),),
            goals=("fulfill_orders",),
            policies=("check_stock",),
            trust_level=0.95,
            ownership="shareholders",
        )
        d = original.to_dict()
        restored = ActorProfile.from_dict(d)
        assert restored.identity.name == "Bob"
        assert restored.identity.actor_type == ActorType.ENTERPRISE
        assert len(restored.capabilities) == 1
        assert restored.capabilities[0].level == CapabilityLevel.MASTER
        assert restored.goals == ("fulfill_orders",)
        assert restored.trust_level == 0.95
        assert restored.ownership == "shareholders"

    def test_actor_role_roundtrip(self):
        original = ActorRole(name="worker", permissions=("pick", "stow"), constraints=("no_heavy_lift",))
        d = original.to_dict()
        restored = ActorRole.from_dict(d)
        assert restored.name == "worker"
        assert restored.permissions == ("pick", "stow")
        assert restored.constraints == ("no_heavy_lift",)

    def test_actor_membership_roundtrip(self):
        original = ActorMembership(actor_id="a1", society_id="s1", role=ActorRole(name="worker"))
        d = original.to_dict()
        restored = ActorMembership.from_dict(d)
        assert restored.actor_id == "a1"
        assert restored.society_id == "s1"
        assert restored.role.name == "worker"

    def test_actor_relationship_roundtrip(self):
        original = ActorRelationship(
            source_actor_id="a1",
            target_actor_id="a2",
            relationship_type=RelationshipType.CUSTOMER,
            strength=0.8,
        )
        d = original.to_dict()
        restored = ActorRelationship.from_dict(d)
        assert restored.source_actor_id == "a1"
        assert restored.target_actor_id == "a2"
        assert restored.relationship_type == RelationshipType.CUSTOMER
        assert restored.strength == 0.8

    def test_actor_address_roundtrip(self):
        original = ActorAddress(
            actor_id="a1",
            address_type="email",
            value="bob@example.com",
            is_primary=True,
        )
        d = original.to_dict()
        restored = ActorAddress.from_dict(d)
        assert restored.address_type == "email"
        assert restored.value == "bob@example.com"
        assert restored.is_primary is True

    def test_society_roundtrip(self):
        original = Society(
            society_id="test_society",
            name="Test Society",
            description="A test",
            memberships=(ActorMembership(actor_id="a1", role=ActorRole(name="worker")),),
            relationships=(ActorRelationship(source_actor_id="a1", target_actor_id="a2"),),
            addresses=(ActorAddress(actor_id="a1", address_type="urn", value="urn:test:1"),),
            policies=("rule1", "rule2"),
        )
        d = original.to_dict()
        restored = Society.from_dict(d)
        assert restored.society_id == "test_society"
        assert restored.name == "Test Society"
        assert len(restored.memberships) == 1
        assert restored.memberships[0].role.name == "worker"
        assert len(restored.relationships) == 1
        assert len(restored.addresses) == 1
        assert restored.policies == ("rule1", "rule2")

    def test_json_safe(self):
        import json

        society = Society(
            name="JSON Test",
            memberships=(ActorMembership(actor_id="a1", role=ActorRole(name="r1")),),
        )
        json_str = json.dumps(society.to_dict(), indent=2)
        restored = Society.from_dict(json.loads(json_str))
        assert restored.name == "JSON Test"
        assert restored.memberships[0].role.name == "r1"


class TestWorldSerialization:
    def test_world_entity_roundtrip(self):
        original = WorldEntity(
            name="Store A",
            entity_type=WorldEntityType.ENTITY,
            attributes={"stock": 10},
            confidence=0.95,
            provenance="inventory",
        )
        d = original.to_dict()
        restored = WorldEntity.from_dict(d)
        assert restored.entity_id == original.entity_id
        assert restored.name == "Store A"
        assert restored.entity_type == WorldEntityType.ENTITY
        assert restored.attributes == {"stock": 10}
        assert restored.confidence == 0.95
        assert restored.provenance == "inventory"

    def test_world_relationship_roundtrip(self):
        original = WorldRelationship(source_id="e1", target_id="e2", kind=RelationshipKind.DEPENDS_ON)
        d = original.to_dict()
        restored = WorldRelationship.from_dict(d)
        assert restored.source_id == "e1"
        assert restored.target_id == "e2"
        assert restored.kind == RelationshipKind.DEPENDS_ON

    def test_world_event_roundtrip(self):
        original = WorldEvent(event_type=EventType.OBSERVATION, entity_id="e1", description="observed")
        d = original.to_dict()
        restored = WorldEvent.from_dict(d)
        assert restored.event_type == EventType.OBSERVATION
        assert restored.entity_id == "e1"

    def test_world_resource_roundtrip(self):
        original = WorldResource(name="Milk", resource_type="dairy", quantity=10, unit="liters")
        d = original.to_dict()
        restored = WorldResource.from_dict(d)
        assert restored.name == "Milk"
        assert restored.quantity == 10

    def test_world_capability_roundtrip(self):
        original = WorldCapability(name="Delivery", requirements=("robot", "route_planning"))
        d = original.to_dict()
        restored = WorldCapability.from_dict(d)
        assert restored.name == "Delivery"
        assert restored.requirements == ("robot", "route_planning")

    def test_world_location_roundtrip(self):
        original = WorldLocation(name="Store A", address="456 Oak Ave", latitude=37.77, longitude=-122.42)
        d = original.to_dict()
        restored = WorldLocation.from_dict(d)
        assert restored.name == "Store A"
        assert restored.latitude == 37.77

    def test_world_policy_roundtrip(self):
        original = WorldPolicy(name="Inventory Check", rules=("check_stock",), scope="retailer")
        d = original.to_dict()
        restored = WorldPolicy.from_dict(d)
        assert restored.name == "Inventory Check"
        assert restored.rules == ("check_stock",)

    def test_shared_world_roundtrip(self):
        world = SharedWorld()
        world.add_entity(WorldEntity(name="Store A", attributes={"stock": 10}))
        world.add_entity(WorldEntity(name="Warehouse"))
        world.add_relationship(WorldRelationship(source_id="e1", target_id="e2", kind=RelationshipKind.DEPENDS_ON))
        world.record_event(WorldEvent(event_type=EventType.OBSERVATION, description="observed"))
        world.add_resource(WorldResource(name="Milk", quantity=10))
        world.add_capability(WorldCapability(name="Delivery"))
        world.add_location(WorldLocation(name="Store A", latitude=37.77))
        world.add_policy(WorldPolicy(name="Check stock"))

        d = world.to_dict()
        restored = SharedWorld.from_dict(d)

        assert restored.version == world.version
        assert len(restored.entities()) == 2
        assert len(restored.relationships_for("e1")) == 1
        assert len(restored.events()) == 1
        assert len(restored.resources()) == 1
        assert len(restored.capabilities()) == 1
        assert len(restored.locations()) == 1
        assert len(restored.policies()) == 1
        assert restored.entities()[0].name == "Store A"
        assert restored.entities()[0].attributes == {"stock": 10}

    def test_shared_world_json_safe(self):
        import json

        world = SharedWorld()
        world.add_entity(WorldEntity(name="Test", entity_type=WorldEntityType.RESOURCE, confidence=0.9))
        json_str = json.dumps(world.to_dict(), indent=2)
        restored = SharedWorld.from_dict(json.loads(json_str))
        assert len(restored.entities()) == 1
        assert restored.entities()[0].name == "Test"
        assert restored.entities()[0].confidence == 0.9

    def test_full_milk_delivery_society_serialization(self):
        """End-to-end: build the acceptance-criteria society, serialize,
        deserialize, verify identical structure."""
        society = Society(
            society_id="milk_delivery",
            name="Milk Delivery Society",
            memberships=(
                ActorMembership(
                    actor_id="human_1",
                    role=ActorRole(name="customer", permissions=("order",)),
                ),
                ActorMembership(
                    actor_id="retail_1",
                    role=ActorRole(name="retailer", permissions=("inventory", "fulfill")),
                ),
                ActorMembership(
                    actor_id="robot_1",
                    role=ActorRole(name="retrieval_robot", permissions=("pick",)),
                ),
            ),
            relationships=(
                ActorRelationship(
                    source_actor_id="human_1",
                    target_actor_id="retail_1",
                    relationship_type=RelationshipType.CUSTOMER,
                ),
                ActorRelationship(
                    source_actor_id="retail_1",
                    target_actor_id="robot_1",
                    relationship_type=RelationshipType.SUPERIOR,
                ),
            ),
            policies=("no_late_delivery",),
        )
        world = SharedWorld()
        store = WorldEntity(
            name="Store A",
            attributes={"milk_stock": 10},
            confidence=0.95,
            provenance="inventory_system",
        )
        world.add_entity(store)
        world.add_resource(WorldResource(name="Milk", quantity=10, unit="liters", location_id=store.entity_id))

        import json

        society_json = json.dumps(society.to_dict(), indent=2)
        world_json = json.dumps(world.to_dict(), indent=2)

        society_restored = Society.from_dict(json.loads(society_json))
        world_restored = SharedWorld.from_dict(json.loads(world_json))

        assert society_restored.name == "Milk Delivery Society"
        assert len(society_restored.memberships) == 3
        assert len(society_restored.relationships) == 2
        assert society_restored.policies == ("no_late_delivery",)

        assert len(world_restored.entities()) == 1
        assert world_restored.entities()[0].name == "Store A"
        assert world_restored.entities()[0].attributes["milk_stock"] == 10
        assert len(world_restored.resources()) == 1
        assert world_restored.resources()[0].quantity == 10
