"""Phase 10: Autonomous Actor Scheduler - Continuous Ticking

Manages independent cognitive loop execution for all actors.
Society Runtime schedules, not orchestrates cognition.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.shared.actor_protocols import AutonomousActorProtocol


@dataclass
class ActorTickConfig:
    """Configuration for actor's cognitive ticking"""

    actor_id: str
    tick_interval: float = 0.1  # 100ms default
    enabled: bool = True
    max_consecutive_errors: int = 5
    backoff_multiplier: float = 2.0


class ActorScheduler:
    """Manages autonomous execution of all actors

    Responsibilities:
    - Schedule independent cognitive loops for each actor
    - Manage tick frequency per actor
    - Handle actor failures and backoff
    - Coordinate startup/shutdown
    """

    def __init__(self, world=None):
        self._world = world
        self._actors: Dict[str, AutonomousActorProtocol] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._configs: Dict[str, ActorTickConfig] = {}
        self._error_counts: Dict[str, int] = {}
        self._last_tick: Dict[str, datetime] = {}
        self._shutdown = False
        self._executor_task: Optional[asyncio.Task] = None

    def set_world(self, world) -> None:
        """Set world reference for all registered and future actors"""
        self._world = world
        for actor in self._actors.values():
            actor.set_world(world)

    def register_actor(self, actor: AutonomousActorProtocol, config: Optional[ActorTickConfig] = None) -> None:
        """Register actor for autonomous execution"""
        actor_id = actor.actor_id

        # Set world reference
        actor.set_world(self._world)

        # Store actor and configuration
        self._actors[actor_id] = actor
        if config is None:
            config = ActorTickConfig(actor_id=actor_id)
        self._configs[actor_id] = config
        self._error_counts[actor_id] = 0

    def unregister_actor(self, actor_id: str) -> None:
        """Remove actor from scheduler"""
        if actor_id in self._tasks:
            task = self._tasks.pop(actor_id)
            task.cancel()

        self._actors.pop(actor_id, None)
        self._configs.pop(actor_id, None)
        self._error_counts.pop(actor_id, None)
        self._last_tick.pop(actor_id, None)

    def set_tick_interval(self, actor_id: str, interval: float) -> None:
        """Adjust tick frequency for actor"""
        if actor_id in self._configs:
            self._configs[actor_id].tick_interval = interval

    def enable_actor(self, actor_id: str) -> None:
        """Enable cognitive loop for actor"""
        if actor_id in self._configs:
            self._configs[actor_id].enabled = True

    def disable_actor(self, actor_id: str) -> None:
        """Disable cognitive loop for actor"""
        if actor_id in self._configs:
            self._configs[actor_id].enabled = False

    async def start(self) -> None:
        """Start scheduler and begin actor ticking"""
        if self._executor_task:
            return  # Already running

        self._shutdown = False
        self._executor_task = asyncio.create_task(self._schedule_loop())

    async def stop(self) -> None:
        """Stop scheduler and shutdown all actors"""
        self._shutdown = True

        # Wait for executor to finish
        if self._executor_task:
            try:
                await asyncio.wait_for(self._executor_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._executor_task.cancel()

        # Shutdown all actors
        shutdown_tasks = [actor.shutdown() for actor in self._actors.values()]
        if shutdown_tasks:
            await asyncio.gather(*shutdown_tasks, return_exceptions=True)

        # Cancel all tick tasks
        for task in self._tasks.values():
            task.cancel()

        self._tasks.clear()
        self._executor_task = None

    async def _schedule_loop(self) -> None:
        """Main scheduling loop

        Continuously checks which actors should tick and schedules them
        """
        while not self._shutdown:
            try:
                now = datetime.now()

                # Check each actor to see if it should tick
                for actor_id, config in self._configs.items():
                    if not config.enabled:
                        continue

                    if actor_id not in self._actors:
                        continue

                    # Check if enough time has passed since last tick
                    last_tick = self._last_tick.get(actor_id)
                    if last_tick and (now - last_tick).total_seconds() < config.tick_interval:
                        continue

                    # Schedule tick if not already running
                    if actor_id not in self._tasks or self._tasks[actor_id].done():
                        actor = self._actors[actor_id]
                        task = asyncio.create_task(self._tick_actor(actor))
                        self._tasks[actor_id] = task

                # Small sleep to prevent busy-waiting
                await asyncio.sleep(0.01)

            except Exception as e:
                print(f"Scheduler error: {e}")
                await asyncio.sleep(0.1)

    async def _tick_actor(self, actor: AutonomousActorProtocol) -> None:
        """Execute one cognitive cycle for actor"""
        actor_id = actor.id
        config = self._configs[actor_id]

        try:
            # Execute cognitive loop cycle
            await self._run_cognitive_cycle(actor)

            # Reset error count on success
            self._error_counts[actor_id] = 0
            self._last_tick[actor_id] = datetime.now()

        except asyncio.CancelledError:
            # Task was cancelled, ignore
            pass

        except Exception as e:
            # Handle error with backoff
            self._error_counts[actor_id] += 1
            print(f"Actor {actor_id} tick error: {e}")

            if self._error_counts[actor_id] >= config.max_consecutive_errors:
                # Too many errors, disable actor
                print(f"Actor {actor_id} disabled after {config.max_consecutive_errors} errors")
                config.enabled = False
            else:
                # Increase backoff interval
                backoff_interval = config.tick_interval * (config.backoff_multiplier ** self._error_counts[actor_id])
                self._last_tick[actor_id] = datetime.now() + timedelta(seconds=backoff_interval)

    async def _run_cognitive_cycle(self, actor: AutonomousActorProtocol) -> None:
        """Execute one full cognitive cycle for actor

        This is the core of Phase 8-10: moving cycle execution to actor
        """
        await actor._cognitive_tick()
        actor._cognitive_state.tick_count += 1
        actor._cognitive_state.last_tick = datetime.now()

    def get_actor_stats(self, actor_id: str) -> Optional[Dict]:
        """Get execution statistics for actor"""
        if actor_id not in self._actors:
            return None

        actor = self._actors[actor_id]
        config = self._configs[actor_id]

        return {
            "actor_id": actor_id,
            "tick_count": actor._cognitive_state.tick_count,
            "last_tick": actor._cognitive_state.last_tick,
            "enabled": config.enabled,
            "tick_interval": config.tick_interval,
            "error_count": self._error_counts[actor_id],
            "converged": actor._cognitive_state.converged,
        }

    def get_all_stats(self) -> Dict[str, Dict]:
        """Get statistics for all registered actors"""
        return {actor_id: self.get_actor_stats(actor_id) for actor_id in self._actors.keys()}

    def get_active_actor_count(self) -> int:
        """Number of actors currently enabled"""
        return sum(1 for config in self._configs.values() if config.enabled)

    def get_total_actor_count(self) -> int:
        """Total number of registered actors"""
        return len(self._actors)
