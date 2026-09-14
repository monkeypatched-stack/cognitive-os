"""Phase 8: Society Runtime Updated for Autonomous Actors

Step 13.2 (ACP-1): legacy/test-only. The canonical coordinator is
kernel/society/runtime.py::SocietyRuntime (used by PlanetaryRuntime and every
live route). SocietyRuntimePhase8 predates that consolidation and today is
reachable only from tests/test_phase8_autonomous_actors.py, for its
ActorScheduler-based tick-interval scheduling. Kept for that test coverage,
not the active coordination path.

Society Runtime becomes a coordinator, not an orchestrator of cognition.
Actors execute cognitive loops autonomously; Society Runtime manages:
- Actor lifecycle
- Event routing (Context Stream)
- World state (read-only to actors)
- Coordination between actors
"""

from __future__ import annotations

from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime

from src.actor.autonomous_actor import AutonomousActor
from src.monkey_brain.kernel.compile.solid_interfaces import ServiceInterface
from src.monkey_brain.kernel.compile.runtime_interface import RequestContext
from src.monkey_brain.kernel.actor_scheduler import ActorScheduler, ActorTickConfig


@dataclass
class ActorCoordinationEvent:
    """Event for actor-to-actor coordination"""

    event_type: str  # 'observation', 'action', 'learning', 'prediction'
    source_actor_id: str
    data: Dict[str, Any]
    timestamp: datetime


class SocietyRuntimePhase8(ServiceInterface):
    """Updated Society Runtime for Phase 8

    Coordination Model:
    - Actors execute cognitive loops autonomously
    - Context Stream is only world evolution mechanism
    - Society Runtime manages scheduler, not cognition
    """

    def __init__(self):
        self._actors: Dict[str, AutonomousActor] = {}
        self._scheduler: Optional[ActorScheduler] = None
        self._world = None
        self._event_subscribers: Dict[str, List] = {}
        self._runtime_context: Optional[RequestContext] = None
        self._cognitive_runtime = None

    async def boot(self, app: Any) -> None:
        """Initialize Society Runtime"""
        # Create scheduler for autonomous actor execution
        self._scheduler = ActorScheduler(self._world)
        await self._scheduler.start()

    async def shutdown(self, app: Any) -> None:
        """Shutdown Society Runtime gracefully"""
        if self._scheduler:
            await self._scheduler.stop()
        self._actors.clear()

    async def execute(self, command: Any) -> Any:
        """Execute a command (ServiceInterface requirement)."""
        return await self.process_actor_tick(command) if command else None

    def register_actor(self, actor: AutonomousActor, tick_interval: float = 0.1) -> None:
        """Register actor for autonomous execution

        Phase 8 change: Society Runtime no longer orchestrates cognition.
        It only schedules autonomous execution.
        """
        # Auto-create scheduler if needed
        if self._scheduler is None:
            self._scheduler = ActorScheduler(self._world)

        # Register with scheduler
        config = ActorTickConfig(actor_id=actor.id, tick_interval=tick_interval, enabled=True)
        self._scheduler.register_actor(actor, config)

        # Track actor
        self._actors[actor.id] = actor

        # Link to society runtime for event publishing
        actor.set_society_runtime(self)

    def unregister_actor(self, actor_id: str) -> None:
        """Remove actor from Society Runtime"""
        if self._scheduler:
            self._scheduler.unregister_actor(actor_id)
        self._actors.pop(actor_id, None)

    def set_world(self, world: Any) -> None:
        """Set reference to global world

        World is read-only from actor perspective.
        Updates only via Context Stream.
        """
        self._world = world
        if self._scheduler:
            self._scheduler._world = world

        # Update world reference for all registered actors
        for actor in self._actors.values():
            actor.set_world(world)

    def set_cognitive_runtime(self, runtime: "CognitiveRuntimeInterface") -> None:
        """Set cognitive runtime reference (for backward compatibility)"""
        self._cognitive_runtime = runtime

    def get_cognitive_runtime(self) -> Optional["CognitiveRuntimeInterface"]:
        """Get cognitive runtime (for backward compatibility)"""
        return self._cognitive_runtime

    async def publish_event(self, event: Dict[str, Any]) -> None:
        """Publish event to Context Stream

        Phase 8 change: This is the ONLY mechanism for world evolution.
        Events published here update the global world.
        """
        # Emit to context stream subscribers
        event_type = event.get("event_type", "unknown")
        subscribers = self._event_subscribers.get(event_type, [])

        for subscriber in subscribers:
            try:
                await subscriber(event)
            except Exception as e:
                print(f"Event subscriber error: {e}")

    def subscribe_to_events(self, event_type: str, callback) -> None:
        """Subscribe to context stream events

        Phase 8: Actors can subscribe to events relevant to their goals
        """
        if event_type not in self._event_subscribers:
            self._event_subscribers[event_type] = []
        self._event_subscribers[event_type].append(callback)

    def get_actor(self, actor_id: str) -> Optional[AutonomousActor]:
        """Get actor by ID"""
        return self._actors.get(actor_id)

    def get_all_actors(self) -> List[AutonomousActor]:
        """Get all registered actors"""
        return list(self._actors.values())

    def get_actor_count(self) -> int:
        """Number of registered actors"""
        return len(self._actors)

    def get_active_actor_count(self) -> int:
        """Number of actors currently executing cognitive loops"""
        if self._scheduler:
            return self._scheduler.get_active_actor_count()
        return 0

    # Scheduler management

    def enable_actor_cognition(self, actor_id: str) -> None:
        """Enable cognitive loop for actor"""
        if self._scheduler:
            self._scheduler.enable_actor(actor_id)

    def disable_actor_cognition(self, actor_id: str) -> None:
        """Disable cognitive loop for actor"""
        if self._scheduler:
            self._scheduler.disable_actor(actor_id)

    def set_actor_tick_interval(self, actor_id: str, interval: float) -> None:
        """Adjust cognitive tick frequency for actor"""
        if self._scheduler:
            self._scheduler.set_tick_interval(actor_id, interval)

    # Monitoring

    def get_actor_stats(self, actor_id: str) -> Optional[Dict[str, Any]]:
        """Get execution stats for actor"""
        if self._scheduler:
            return self._scheduler.get_actor_stats(actor_id)
        return None

    def get_all_actor_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get stats for all actors"""
        if self._scheduler:
            return self._scheduler.get_all_stats()
        return {}

    # Coordination helpers

    async def coordinate_multi_actor_goal(self, goal_description: str, actor_ids: List[str]) -> bool:
        """Coordinate goal execution across multiple actors

        Phase 11 feature: Enables collaborative planning
        """
        # Create shared goal event
        coordination_event = {
            "event_type": "shared_goal",
            "goal": goal_description,
            "actors": actor_ids,
            "timestamp": datetime.now(),
        }

        await self.publish_event(coordination_event)
        return True

    async def wait_for_actor_convergence(self, actor_id: str, timeout: float = 30.0) -> bool:
        """Wait for actor's beliefs to converge

        Useful for checking when actor has stabilized its cognition
        """
        import asyncio

        start_time = datetime.now()

        while True:
            if actor_id not in self._actors:
                return False

            actor = self._actors[actor_id]
            if actor._cognitive_state.converged:
                return True

            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed > timeout:
                return False

            await asyncio.sleep(0.1)

    def get_world(self) -> Optional[Any]:
        """Get reference to global world (read-only)"""
        return self._world
