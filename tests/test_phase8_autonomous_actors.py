"""Phase 8: Autonomous Actors - Integration Tests

Tests verify that actors execute cognitive loops independently
without Pipeline orchestration.
"""

import pytest
import asyncio
from datetime import datetime

from src.actor.autonomous_actor import AutonomousActor, CognitiveLoopResult
from src.monkey_brain.kernel.actor_scheduler import ActorScheduler, ActorTickConfig
from src.monkey_brain.kernel.compile.society_runtime_phase8 import SocietyRuntimePhase8
from src.monkey_brain.kernel.compile.entity import Person, Enterprise


class MockWorld:
    """Mock world for testing"""

    def __init__(self):
        self.entities = []
        self.transitions = []
        self.state = {}

    def get_state(self):
        return self.state.copy()

    def apply_action(self, action):
        self.state.update({"action": action, "timestamp": datetime.now()})
        return self.state.copy()

    def clone(self):
        import copy

        clone = MockWorld()
        clone.entities = copy.deepcopy(self.entities)
        clone.transitions = copy.deepcopy(self.transitions)
        clone.state = copy.deepcopy(self.state)
        return clone


class TestAutonomousActorBasics:
    """Test autonomous actor creation and initialization"""

    def test_create_autonomous_actor(self):
        """Autonomous actor can be created from base entity"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        assert actor.id == "actor1"
        assert actor._cognitive_state.tick_count == 0
        assert not actor._shutdown

    def test_no_duplicate_cognitive_identity(self):
        """Step 13.5: AutonomousActor (== ActorSystem) must own exactly one
        CognitiveActor identity. Previously ActorRuntime constructed a
        second, disjoint Actor(actor_id) internally — one actor_id backed
        two independent belief/policy pairs (tick() acted on self, run()
        acted on the separate internal one). Now ActorRuntime reuses the
        outer instance via existing_actor=self."""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        assert actor._actor_runtime is not None
        assert actor._actor_runtime._actor is actor
        assert actor._actor_runtime.belief is actor.belief
        assert actor._actor_runtime._actor.policy is actor.policy

    def test_set_world_reference(self):
        """Actor can reference global world"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)
        world = MockWorld()

        actor.set_world(world)
        assert actor._world is world

    def test_set_society_runtime(self):
        """Actor can reference Society Runtime"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)
        society = SocietyRuntimePhase8()

        actor.set_society_runtime(society)
        assert actor._society_runtime is society


class TestCognitiveLoopStages:
    """Test individual stages of cognitive loop"""

    @pytest.mark.asyncio
    async def test_observe_stage(self):
        """Observe stage generates observations"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)
        world = MockWorld()
        world.entities = [entity]

        actor.set_world(world)
        observations = await actor.observe(world)

        assert "visible_entities" in observations
        assert "relevant_transitions" in observations
        assert "timestamp" in observations

    @pytest.mark.asyncio
    async def test_believe_stage(self):
        """Believe stage updates beliefs from observations"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        observations = {"state": "initialized"}
        updated = await actor.believe(observations)

        # Mock actor has no real belief model, so returns False
        assert isinstance(updated, bool)

    @pytest.mark.asyncio
    async def test_plan_stage(self):
        """Plan stage generates plan from beliefs"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        plan = await actor.plan()

        assert "start_state" in plan
        assert "goal_states" in plan
        assert "steps" in plan
        assert "constraints" in plan

    @pytest.mark.asyncio
    async def test_execute_stage(self):
        """Execute stage performs actions"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        plan = {"steps": [{"action": "move"}]}
        actions = await actor.execute(plan)

        assert isinstance(actions, list)

    @pytest.mark.asyncio
    async def test_simulate_stage(self):
        """Simulate stage predicts outcomes without mutation"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)
        world = MockWorld()

        actor.set_world(world)
        actions = [{"action": "test"}]

        predictions = await actor.simulate(actions)

        assert "actions_simulated" in predictions
        assert "predicted_state_trajectory" in predictions
        assert "predicted_rewards" in predictions

    @pytest.mark.asyncio
    async def test_compare_stage(self):
        """Compare stage identifies prediction error"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        predicted = {
            "predicted_state_trajectory": [{"state": "a"}, {"state": "b"}],
            "predicted_rewards": [1.0, 2.0],
        }
        actual = {
            "state_trajectory": [{"state": "a"}, {"state": "c"}],
            "actual_rewards": [1.0, 1.5],
        }

        comparison = await actor.compare(predicted, actual)

        assert "trajectory_error" in comparison
        assert "reward_error" in comparison
        assert "model_accurate" in comparison

    @pytest.mark.asyncio
    async def test_learn_stage(self):
        """Learn stage updates cognition from error"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        comparison = {"trajectory_error": 0.05, "model_accurate": True}
        learned = await actor.learn(comparison)

        assert isinstance(learned, bool)

    @pytest.mark.asyncio
    async def test_compile_phi_stage(self):
        """Compile Φ stage creates efficient operator"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        phi = await actor.compile_phi()

        assert phi is None or isinstance(phi, dict)

    @pytest.mark.asyncio
    async def test_predict_stage(self):
        """Predict stage forecasts future"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)
        world = MockWorld()

        actor.set_world(world)
        prediction = await actor.predict(world)

        assert "horizon" in prediction
        assert "states" in prediction
        assert "confidence" in prediction


class TestAutonomousExecution:
    """Test continuous autonomous execution"""

    @pytest.mark.asyncio
    async def test_full_cognitive_cycle(self):
        """Full cognitive cycle executes all stages"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)
        world = MockWorld()

        actor.set_world(world)
        result = await actor._cognitive_tick()

        assert isinstance(result, CognitiveLoopResult)
        assert result.tick == 0
        assert "observations" in result.__dict__
        assert "plan" in result.__dict__
        assert "actions" in result.__dict__

    @pytest.mark.asyncio
    async def test_continuous_ticking(self):
        """Actor ticks continuously without external requests"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)
        world = MockWorld()

        actor.set_world(world)
        actor._tick_duration = 0.05  # 50ms for faster test

        # Start cognitive loop
        loop_task = asyncio.create_task(actor.execute_cognitive_loop())

        # Let it run for 200ms (should get ~4 ticks)
        await asyncio.sleep(0.2)

        # Stop the loop
        await actor.shutdown()
        await asyncio.sleep(0.1)  # Give loop time to exit

        assert actor._cognitive_state.tick_count > 0


class TestActorScheduler:
    """Test scheduler managing multiple autonomous actors"""

    @pytest.mark.asyncio
    async def test_register_actor(self):
        """Actor can be registered with scheduler"""
        scheduler = ActorScheduler()
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        scheduler.register_actor(actor)

        assert actor.id in scheduler._actors
        assert actor.id in scheduler._configs

    @pytest.mark.asyncio
    async def test_unregister_actor(self):
        """Actor can be unregistered from scheduler"""
        scheduler = ActorScheduler()
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        scheduler.register_actor(actor)
        scheduler.unregister_actor(actor.id)

        assert actor.id not in scheduler._actors

    @pytest.mark.asyncio
    async def test_multiple_actors_scheduled(self):
        """Multiple actors execute independently"""
        scheduler = ActorScheduler()
        world = MockWorld()

        entity1 = Person(id="actor1", name="John")
        entity2 = Person(id="actor2", name="Jane")

        actor1 = AutonomousActor(entity1)
        actor2 = AutonomousActor(entity2)

        scheduler.set_world(world)
        scheduler.register_actor(actor1, ActorTickConfig(actor_id="actor1", tick_interval=0.05))
        scheduler.register_actor(actor2, ActorTickConfig(actor_id="actor2", tick_interval=0.05))

        await scheduler.start()
        await asyncio.sleep(0.2)
        await scheduler.stop()

        # Both actors should have ticked
        assert scheduler._actors["actor1"]._cognitive_state.tick_count > 0
        assert scheduler._actors["actor2"]._cognitive_state.tick_count > 0

    @pytest.mark.asyncio
    async def test_scheduler_enable_disable(self):
        """Actor can be enabled/disabled dynamically"""
        scheduler = ActorScheduler()
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        scheduler.register_actor(actor)
        scheduler.enable_actor(actor.id)
        assert scheduler._configs[actor.id].enabled

        scheduler.disable_actor(actor.id)
        assert not scheduler._configs[actor.id].enabled

    @pytest.mark.asyncio
    async def test_scheduler_tick_interval(self):
        """Tick interval can be adjusted per actor"""
        scheduler = ActorScheduler()
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        scheduler.register_actor(actor, ActorTickConfig(actor_id="actor1", tick_interval=0.1))
        scheduler.set_tick_interval(actor.id, 0.2)

        assert scheduler._configs[actor.id].tick_interval == 0.2


class TestSocietyRuntimePhase8:
    """Test Society Runtime coordination of autonomous actors"""

    @pytest.mark.asyncio
    async def test_register_actor_with_society(self):
        """Actor can be registered with Society Runtime"""
        society = SocietyRuntimePhase8()
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        society.register_actor(actor)

        assert actor.id in society._actors
        assert society.get_actor(actor.id) is actor

    @pytest.mark.asyncio
    async def test_society_creates_scheduler(self):
        """Society Runtime creates and manages scheduler"""
        society = SocietyRuntimePhase8()
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        society.register_actor(actor)

        assert society._scheduler is not None

    @pytest.mark.asyncio
    async def test_society_boot_shutdown(self):
        """Society Runtime can boot and shutdown"""
        society = SocietyRuntimePhase8()

        await society.boot(None)
        assert society._scheduler is not None

        await society.shutdown(None)

    @pytest.mark.asyncio
    async def test_event_publishing(self):
        """Society Runtime publishes events to Context Stream"""
        society = SocietyRuntimePhase8()
        received_events = []

        async def test_subscriber(event):
            received_events.append(event)

        society.subscribe_to_events("test", test_subscriber)
        event = {"event_type": "test", "data": "value"}

        await society.publish_event(event)

        assert len(received_events) == 1
        assert received_events[0] == event

    @pytest.mark.asyncio
    async def test_actor_tick_control(self):
        """Society Runtime controls actor tick frequency"""
        society = SocietyRuntimePhase8()
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        society.register_actor(actor, tick_interval=0.1)
        society.set_actor_tick_interval(actor.id, 0.2)

        assert society._scheduler._configs[actor.id].tick_interval == 0.2

    @pytest.mark.asyncio
    async def test_get_actor_stats(self):
        """Society Runtime reports actor execution stats"""
        society = SocietyRuntimePhase8()
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        society.register_actor(actor)
        stats = society.get_actor_stats(actor.id)

        assert stats is not None
        assert "tick_count" in stats
        assert "enabled" in stats


class TestIndependentActorExecution:
    """Test that actors execute independently of Pipeline"""

    @pytest.mark.asyncio
    async def test_no_request_needed_for_cognition(self):
        """Actor executes cognitive loop without external request"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)
        world = MockWorld()

        actor.set_world(world)
        actor._tick_duration = 0.05

        # No Pipeline request - actor executes autonomously
        loop_task = asyncio.create_task(actor.execute_cognitive_loop())

        await asyncio.sleep(0.15)
        await actor.shutdown()

        # Verify multiple cycles executed without any request
        assert actor._cognitive_state.tick_count > 0

    @pytest.mark.asyncio
    async def test_multiple_actors_independent(self):
        """Multiple actors execute independently of each other"""
        world = MockWorld()
        scheduler = ActorScheduler(world)

        # Create 3 actors
        actors = [AutonomousActor(Person(id=f"actor{i}", name=f"Actor{i}")) for i in range(3)]

        for actor in actors:
            scheduler.register_actor(actor, ActorTickConfig(actor_id=actor.id, tick_interval=0.05))

        await scheduler.start()
        await asyncio.sleep(0.2)
        await scheduler.stop()

        # All actors should have executed independent cycles
        for actor in actors:
            assert actor._cognitive_state.tick_count > 0


class TestBackwardCompatibility:
    """Test that Phase 8 works alongside existing Pipeline"""

    def test_autonomous_actor_inherits_from_entity(self):
        """Autonomous actor is compatible with entity interface"""
        entity = Person(id="actor1", name="John")
        actor = AutonomousActor(entity)

        # Should work with entity interface expectations
        assert hasattr(actor, "id")
        assert hasattr(actor, "entity_type")

    @pytest.mark.asyncio
    async def test_society_runtime_backward_compatible(self):
        """New Society Runtime works with old CognitiveRuntime"""
        from src.monkey_brain.kernel.cognitive_runtime import CognitiveRuntime

        society = SocietyRuntimePhase8()
        cognitive = CognitiveRuntime()

        # Should be able to link old and new
        society.set_cognitive_runtime(cognitive)
        assert society.get_cognitive_runtime() is cognitive
