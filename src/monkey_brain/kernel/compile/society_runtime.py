"""CompileSocietyRuntime — legacy actor-id-keyed society orchestrator.

Step 13.2 (ACP-1): this is NOT the canonical SocietyRuntime — that is
kernel/society/runtime.py::SocietyRuntime, used by PlanetaryRuntime and every
live route (/societies/*, /planet/*). This class predates that consolidation,
uses a structurally different register_actor(actor_id, entity, tenant_id)
signature, and today is reachable only from tests (test_phase3_load_1000_actors.py,
test_phase4_performance.py) for its bulk-register/concurrent-tick/checkpoint
capabilities. Renamed from SocietyRuntime to CompileSocietyRuntime to end the
naming collision — kept, not deleted, since it still has real test coverage.

Manages:
- Multiple actors
- Actor scheduling
- Context propagation
- Event delivery
- Planning coordination

The runtime coordinates cognition.
It does not own cognition.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from src.monkey_brain.kernel.compile.entity import (
    Actor,
    EntityRegistry,
)
from src.monkey_brain.kernel.compile.global_world import GlobalWorld
from src.monkey_brain.kernel.compile.context_stream import ContextStream, ContextEvent
from src.monkey_brain.kernel.compile.event_bus import EventBus
from src.monkey_brain.kernel.compile.runtime_services import RuntimeServices
from src.monkey_brain.kernel.compile.actor_belief import ActorBelief
from src.monkey_brain.kernel.compile.runtime_interface import (
    SocietyRuntimeInterface,
    CognitiveRuntimeInterface,
)

logger = logging.getLogger("agentos.society_runtime")


@dataclass
class ActorState:
    """Runtime state for an actor in the society."""

    actor_id: str
    entity: Actor
    belief: ActorBelief
    tenant_id: str
    last_cycle: float = 0.0
    cycle_count: int = 0
    is_active: bool = True


class CompileSocietyRuntime(SocietyRuntimeInterface):
    """Legacy Society Runtime — orchestrates multiple actors over a shared world.

    Implements SocietyRuntimeInterface (Dependency Inversion Principle):
    - Depends on abstraction, not concrete CognitiveRuntime
    - Can coordinate with any CognitiveRuntimeInterface implementation
    - Decoupled from reasoning engine details

    Manages:
    - Actor lifecycle (lookup-or-create, persistence)
    - Concurrent scheduling
    - Context propagation (ContextStream → World → Actors)
    - Event delivery (EventBus)
    - Runtime Services (Planner, Learning, Prediction, Trust, Memory)

    The runtime coordinates multi-actor execution. It does not own cognition.
    """

    def __init__(self, world: GlobalWorld | None = None) -> None:
        self._world = world or GlobalWorld()
        self._event_bus = EventBus()
        self._context_stream = ContextStream(self._world)
        self._services = RuntimeServices(self._event_bus)
        self._registry = EntityRegistry()
        self._actors: dict[str, ActorState] = {}
        self._schedules: list[Callable] = []
        self._version = 0
        self._cognitive_runtime: CognitiveRuntimeInterface | None = None

        # Subscribe to context stream
        self._context_stream.subscribe(self._on_world_update)

    @classmethod
    async def boot(
        cls,
        app: Any,
        *,
        lemon: Any = None,
        persistence: Any = None,
        event_bus: Any = None,
    ) -> "CompileSocietyRuntime":
        """Phase 3: Boot CompileSocietyRuntime from Kernel.

        Initializes the runtime with Kernel-injected dependencies.
        """
        rt = cls()
        rt._lemon = lemon
        rt._persistence = persistence or getattr(app.state, "_persistence", None)
        rt._external_event_bus = event_bus

        # Wire up external event bus if provided
        if event_bus:
            event_bus.subscribe("world.updated", lambda ev: None)  # Subscribe to world updates

        app.state.society_runtime = rt
        if lemon:
            lemon.info("CompileSocietyRuntime boot complete", component="bootstrap")
        logger.info("CompileSocietyRuntime boot complete (Phase 3)")
        if event_bus:
            await event_bus.publish("society_runtime.booted", {})
        return rt

    # ── Actor Management ──────────────────────────────────────────────────────

    def register_actor(self, actor_id: str, entity: Actor | None = None, tenant_id: str = "default") -> ActorState:
        """Lookup-or-create an actor."""
        if actor_id in self._actors:
            return self._actors[actor_id]

        if entity is None:
            from src.monkey_brain.kernel.compile.entity import Person

            entity = Person(id=actor_id, name=actor_id)

        self._registry.register(entity)
        belief = ActorBelief(actor_id, self._world)
        state = ActorState(
            actor_id=actor_id,
            entity=entity,
            belief=belief,
            tenant_id=tenant_id,
        )
        self._actors[actor_id] = state

        self._event_bus.publish(
            "actor.registered",
            {"actor_id": actor_id, "tenant": tenant_id},
            source="society_runtime",
        )
        logger.info("[society] registered actor %s (tenant=%s)", actor_id, tenant_id)
        return state

    def get_actor(self, actor_id: str) -> ActorState | None:
        return self._actors.get(actor_id)

    def unregister_actor(self, actor_id: str) -> bool:
        """Remove an actor from the society. Returns True if removed."""
        if actor_id in self._actors:
            del self._actors[actor_id]
            self._event_bus.publish("actor.unregistered", {"actor_id": actor_id}, source="society_runtime")
            logger.info("[society] unregistered actor %s", actor_id)
            return True
        return False

    def actors_by_tenant(self, tenant_id: str) -> list[ActorState]:
        return [a for a in self._actors.values() if a.tenant_id == tenant_id]

    def active_actors(self) -> list[ActorState]:
        return [a for a in self._actors.values() if a.is_active]

    def get_cognitive_runtime(self) -> CognitiveRuntimeInterface | None:
        """Get the coordinating CognitiveRuntime instance (Dependency Inversion).

        Returns:
            CognitiveRuntime if initialized, None otherwise
        """
        return self._cognitive_runtime

    def set_cognitive_runtime(self, cognitive: CognitiveRuntimeInterface) -> None:
        """Set the coordinating CognitiveRuntime instance.

        Args:
            cognitive: CognitiveRuntime to coordinate with
        """
        self._cognitive_runtime = cognitive
        logger.info("[society] CognitiveRuntime registered (dependency inversion)")

    @property
    def name(self) -> str:
        """Runtime name identifier."""
        return "society"

    @property
    def health(self) -> dict[str, Any]:
        """Get runtime health status."""
        return {
            "name": "society",
            "status": "healthy",
            "actors": len(self._actors),
            "active_actors": len(self.active_actors()),
            "cognitive": "ready" if self._cognitive_runtime else "pending",
        }

    async def shutdown(self, app: Any) -> None:
        """Gracefully shutdown the runtime."""
        logger.info("[society] Shutting down SocietyRuntime")

    async def execute(self, context: Any, work: Any) -> Any:
        """Execute work within society runtime context.

        Args:
            context: RuntimeContext with request details
            work: Work to execute

        Returns:
            Execution result
        """
        # Delegates to cognitive_cycle
        return self.cognitive_cycle(
            actor_id=work.get("actor_id", "default"),
            start=work.get("start", ""),
            goal=work.get("goal", ""),
            reward=work.get("reward", 1.0),
        )

    def bulk_register_actors(
        self,
        actor_ids: list[str],
        tenant_id: str = "default",
        batch_event_publish: bool = True,
    ) -> list[ActorState]:
        """Phase 4: Bulk register multiple actors efficiently.

        Registers multiple actors with single event publish (vs one per actor).
        Use for high-volume actor creation (e.g., load tests, migrations).

        Args:
            actor_ids: List of actor IDs to register
            tenant_id: Tenant for all actors
            batch_event_publish: If True, publish one bulk event instead of N events

        Returns:
            List of registered ActorState objects
        """
        states = []
        for actor_id in actor_ids:
            # Reuse register_actor logic but suppress individual events
            if actor_id not in self._actors:
                from src.monkey_brain.kernel.compile.entity import Person

                entity = Person(id=actor_id, name=actor_id)
                self._registry.register(entity)
                belief = ActorBelief(actor_id, self._world)
                state = ActorState(
                    actor_id=actor_id,
                    entity=entity,
                    belief=belief,
                    tenant_id=tenant_id,
                )
                self._actors[actor_id] = state
                states.append(state)
            else:
                states.append(self._actors[actor_id])

        # Publish single bulk event if requested (reduces event queue pressure)
        if batch_event_publish and states:
            self._event_bus.publish(
                "actors.bulk_registered",
                {"actor_ids": actor_ids, "count": len(states), "tenant": tenant_id},
                source="society_runtime",
            )
            logger.info(
                "[society] bulk registered %d actors (tenant=%s)",
                len(states),
                tenant_id,
            )
        else:
            # Publish individual events
            for state in states:
                self._event_bus.publish(
                    "actor.registered",
                    {"actor_id": state.actor_id, "tenant": tenant_id},
                    source="society_runtime",
                )

        return states

    # ── Context Stream ────────────────────────────────────────────────────────

    def publish_event(
        self,
        entity: str,
        attribute: str,
        value: Any,
        source: str = "",
        domain: str = "default",
    ) -> None:
        """Publish a context event. Updates the world and notifies actors."""
        event = ContextEvent(
            timestamp=time.time(),
            entity=entity,
            attribute=attribute,
            previous_value=None,
            current_value=value,
            source=source,
            metadata={"domain": domain},
        )
        self._context_stream.publish(event)

    def _on_world_update(self, event: ContextEvent, version: Any) -> None:
        """Handle world update — notify all actors."""
        self._version += 1
        self._event_bus.publish(
            "world.updated",
            {
                "entity": event.entity,
                "attribute": event.attribute,
                "version": self._version,
            },
            source="context_stream",
        )

    # ── Cognitive Cycle ───────────────────────────────────────────────────────

    def cognitive_cycle(self, actor_id: str, start: str, goal: str, *, reward: float = 1.0) -> dict:
        """Run a full cognitive cycle for one actor.

        Observe → Believe → Plan → Execute → Learn → Compile Φ → Predict → Commit
        """
        state = self._actors.get(actor_id)
        if state is None:
            return {"error": f"actor {actor_id} not found"}

        state.cycle_count += 1
        state.last_cycle = time.time()

        belief = state.belief

        # Observe
        self._observe_world(state)

        # Plan (using world + belief + Φ)
        phi = self._services.learning.compile_phi(belief)
        plan = self._services.planner.plan(start, goal, self._world, belief=belief, phi=phi)

        # Execute
        observed = []
        current = start
        for _ in range(plan.steps):
            successors = self._world.successors(current)
            if not successors:
                break
            next_state = successors[0] if isinstance(successors[0], str) else successors[0][0]
            observed.append({"from": current, "to": next_state})

            # Trust-weight the observation
            trust_score = self._services.trust.score("world")
            weighted_reward = reward * trust_score

            belief.observe(current, next_state, reward=weighted_reward, source="execution")
            current = next_state

        # Learn (Bellman update)
        for obs in observed:
            self._services.learning.bellman_update(belief, obs["from"], obs["to"], reward, obs["to"])

            # Store in memory
            self._services.memory.store(
                {
                    "actor": actor_id,
                    "action": obs["from"],
                    "result": obs["to"],
                    "reward": reward,
                    "cycle": state.cycle_count,
                }
            )

        # Compile Φ
        phi = self._services.learning.compile_phi(belief)

        # Predict (on cloned world)
        prediction = self._services.prediction.predict(self._world, start)

        # Commit
        state.belief = belief

        result = {
            "actor_id": actor_id,
            "start": start,
            "goal": goal,
            "reached_goal": current == goal,
            "steps": len(observed),
            "belief_nnz": len(belief._beliefs),
            "bellman_entries": len(belief._bellman),
            "memory_size": len(belief._memory),
            "phi_compiled": phi is not None,
            "predicted_state": prediction.predicted_state,
            "cycle_count": state.cycle_count,
        }

        self._event_bus.publish("actor.cycled", result, source="society_runtime")
        return result

    def _observe_world(self, state: ActorState) -> None:
        """Actor observes the world and updates its belief."""
        world = self._world
        belief = state.belief
        if world is None or world._tensor is None:
            return
        for src, dst in world._tensor:
            if not belief._beliefs.get((src, dst)):
                belief.observe(src, dst, source="observation", domain=world._tensor.domain_of(src))

    # ── Multi-Actor Execution ─────────────────────────────────────────────────

    def run_all(self, start: str, goal: str, *, reward: float = 1.0) -> dict[str, dict]:
        """Run cognitive cycle for all active actors."""
        results = {}
        for state in self.active_actors():
            results[state.actor_id] = self.cognitive_cycle(state.actor_id, start, goal, reward=reward)
        return results

    def schedule(self, callback: Callable) -> None:
        """Register a scheduling callback."""
        self._schedules.append(callback)

    def run_concurrent(self, start: str, goal: str, *, reward: float = 1.0, max_workers: int = 4) -> dict[str, dict]:
        """Run cognitive cycle for all active actors concurrently."""
        import concurrent.futures

        results = {}

        def _run_actor(state: ActorState) -> tuple[str, dict]:
            return state.actor_id, self.cognitive_cycle(state.actor_id, start, goal, reward=reward)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run_actor, s): s for s in self.active_actors()}
            for future in concurrent.futures.as_completed(futures):
                try:
                    actor_id, result = future.result()
                    results[actor_id] = result
                except Exception as e:
                    state = futures[future]
                    results[state.actor_id] = {"error": str(e)}

        return results

    # ── Persistence ────────────────────────────────────────────────────────────

    def checkpoint(self, base_path: str) -> None:
        """Checkpoint all runtime state."""
        import json
        import os

        os.makedirs(base_path, exist_ok=True)

        # Save world
        self._world.save(os.path.join(base_path, "world"))

        # Save actors
        actors_data = {}
        for aid, state in self._actors.items():
            actors_data[aid] = {
                "entity_id": state.entity.id,
                "entity_type": state.entity.entity_type.value,
                "tenant_id": state.tenant_id,
                "belief_nnz": len(state.belief._beliefs),
                "bellman_entries": len(state.belief._bellman),
                "memory_size": len(state.belief._memory),
                "cycle_count": state.cycle_count,
            }
        with open(os.path.join(base_path, "actors.json"), "w") as f:
            json.dump(actors_data, f, indent=2)

        # Save meta
        with open(os.path.join(base_path, "meta.json"), "w") as f:
            json.dump(
                {"version": self._version, "actor_count": len(self._actors)},
                f,
                indent=2,
            )

        logger.info("[society] checkpointed to %s (%d actors)", base_path, len(self._actors))

    def restore(self, base_path: str) -> None:
        """Restore runtime state from checkpoint."""
        import json
        import os

        self._world.load(os.path.join(base_path, "world"))

        actors_path = os.path.join(base_path, "actors.json")
        if os.path.exists(actors_path):
            with open(actors_path) as f:
                actors_data = json.load(f)
            for aid, data in actors_data.items():
                if aid not in self._actors:
                    self.register_actor(aid, tenant_id=data.get("tenant_id", "default"))

        logger.info("[society] restored from %s", base_path)

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def world(self) -> GlobalWorld:
        return self._world

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def services(self) -> RuntimeServices:
        return self._services

    def summary(self) -> dict:
        return {
            "version": self._version,
            "world": self._world.summary() if self._world else {},
            "actors": len(self._actors),
            "active_actors": len(self.active_actors()),
            "tenants": list(set(a.tenant_id for a in self._actors.values())),
            "services": self._services.summary(),
        }
