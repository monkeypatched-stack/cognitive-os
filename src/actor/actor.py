"""ActorSystem — the single canonical Actor class.

Combines:
  - CognitiveActor (beliefs, policy, Φ, cognitive cycle)
  - Action recording via ActorRuntime
  - Async lifecycle (autonomous cognitive loop)

This is the ONLY Actor class. All others are aliases or deprecated.

Architecture:
  ActorSystem extends CognitiveActor extends Entity
    ├── Identity (id, type, attributes, state)
    ├── Beliefs (ActorBelief → PolicyStore)
    ├── Policy (PolicyStore → Q-values)
    ├── Φ (sparse transition operator)
    ├── Memory, Trust, Reward
    ├── Action recording via ActorRuntime
    └── Async cognitive loop (observe → believe → plan → execute → learn)
"""

from __future__ import annotations

import logging
from typing import Any
from datetime import datetime

from src.monkey_brain.kernel.compile.cognitive_actor import (
    CognitiveActor,
    _CognitiveTickResult,
)

# _CognitiveTickResult is re-exported here for src/actor/autonomous_actor.py,
# which imports it from this module (not directly from cognitive_actor.py) as
# CognitiveLoopResult — kept even though this file's own code no longer
# constructs one directly (Step 12.2 removed the duplicate _cognitive_tick()
# that used to build it here).
from src.monkey_brain.kernel.compile.entity import Entity, EntityType

logger = logging.getLogger("agentos.actor")


class ActorSystem(CognitiveActor):
    """The single canonical Actor — cognitive + pipeline + async lifecycle.

    Composes:
      - CognitiveActor (beliefs, policy, Φ, cognitive cycle)
      - ActorRuntime (action recording)
      - Autonomous async cognitive loop

    For new code, use this class directly.
    For backward compat, AutonomousActor is an alias.
    """

    def __init__(
        self,
        entity: Entity | None = None,
        cognitive_runtime: Any = None,
        belief_runtime: Any = None,
        trust_runtime: Any = None,
        context: Any = None,
        local_belief: Any = None,
        world_view: Any = None,
        actor_id: str | None = None,
        entity_type: EntityType = EntityType.ENTITY,
        **kwargs: Any,
    ):
        # Resolve entity_id from entity or explicit arg
        if entity is not None:
            actor_id = actor_id or entity.id
            entity_type = entity.entity_type
        elif actor_id is None:
            from uuid import uuid4

            actor_id = f"actor_{uuid4().hex[:8]}"

        # Initialize CognitiveActor (beliefs, policy, Φ)
        super().__init__(entity_id=actor_id, entity_type=entity_type, **kwargs)
        self._base_entity = entity

        # Pipeline-specific dependencies
        self._cognitive_runtime = cognitive_runtime
        self._belief_runtime = trust_runtime  # kept for backward compat
        self._trust_runtime = trust_runtime
        self.context = context
        self._worldview = world_view

        # CognitionEngine (shared world tensor)
        try:
            from monkey_brain.kernel.cognitive_engine import CognitionEngine

            self._engine = CognitionEngine(0)
        except Exception:
            self._engine = None

        # Load policies
        self._global_policies = self._load_global_policies()
        self._local_policies = self._load_local_policies()

        # Initialize ActorRuntime for pipeline execution
        try:
            from monkey_brain.kernel.compile.actor_runtime import ActorRuntime

            self._actor_runtime = ActorRuntime(
                actor_id,
                cognitive_runtime=self._cognitive_runtime,
                belief_runtime=belief_runtime,
                trust_runtime=trust_runtime,
                context=self.context,
                local_belief=local_belief or self.belief,
                world_view=world_view,
                # Step 13.5: share this ActorSystem's own identity instead of
                # letting ActorRuntime construct a second, disjoint
                # CognitiveActor under the same actor_id (a real, previously
                # undetected fragmentation — tick() acted on self, run() acted
                # on a completely separate belief/policy pair).
                existing_actor=self,
            )
        except Exception as exc:
            logger.warning("ActorRuntime init failed: %s", exc)
            self._actor_runtime = None

    # ── Action execution ──────────────────────────────────────────────────

    async def run(self, request: Any, *, society_runtime: Any = None) -> Any:
        """Record an action via ActorRuntime.

        society_runtime is passed BY the society (the caller).
        The actor belongs to a society — society is not a dependency of the actor.
        """
        if self._actor_runtime is None:
            raise RuntimeError("ActorRuntime not initialized")
        return self._actor_runtime.act(request, actor=self, society_runtime=society_runtime)

    # ── Async cognitive loop (Phase 8) ──────────────────────────────────────
    #
    # Step 12.2: execute_cognitive_loop() and _cognitive_tick() are no longer
    # overridden here — both are now inherited directly from CognitiveActor,
    # which delegates the complete cognitive lifecycle to the canonical
    # engine (belief_runtime.CognitiveRuntime) instead of re-implementing it.
    # This class previously duplicated that loop a second time, calling the
    # public-named stage methods below instead of the parent's private
    # _async_* stubs — removed as confirmed duplication (Step 12 Gap
    # Analysis). The stage methods themselves (observe, believe, plan,
    # execute, learn, compile_phi, simulate, compare, predict, commit) are
    # kept: they have independent direct test coverage
    # (tests/test_phase8_autonomous_actors.py) as standalone units, separate
    # from whatever loop orchestration calls them.

    # ── Async stubs (override in subclasses for real behavior) ───────────────

    async def compile_phi(self):
        """Compile Φ — async wrapper for backward compat with Phase 8 tests."""
        return super().compile_phi()

    async def observe(self, world) -> dict:
        """Observe world state."""
        observations = {}
        if world and hasattr(world, "entities"):
            observations["visible_entities"] = list(world.entities)
        if world and hasattr(world, "transitions"):
            observations["relevant_transitions"] = list(world.transitions)
        observations["timestamp"] = datetime.now()
        return observations

    async def believe(self, observations: dict) -> bool:
        return False

    async def plan(self, goals=None) -> dict:
        return {
            "start_state": {},
            "goal_states": goals or [],
            "steps": [],
            "constraints": {},
        }

    async def execute_actions(self, plan: dict) -> list:
        """Execute plan steps asynchronously (for cognitive loop)."""
        return []

    async def execute(self, plan: Any, world: Any = None, goal: str | None = None, horizon: int = 64) -> list:
        """Execute plan — async for backward compat with Phase 8 tests."""
        if world is not None:
            return super().execute(plan, world, goal, horizon)
        return []

    async def simulate(self, actions: list, world_clone=None) -> dict:
        return {
            "actions_simulated": len(actions),
            "predicted_state_trajectory": [],
            "predicted_rewards": [],
        }

    async def compare(self, predicted: dict, actual: dict) -> dict:
        return {"trajectory_error": 0.0, "reward_error": 0.0, "model_accurate": True}

    async def learn(self, comparison: dict) -> bool:
        return False

    async def predict(self, world=None) -> dict:
        return {"horizon": 5, "states": [], "confidence": 0.0}

    async def commit(self, actions: list) -> None:
        if self._society_runtime:
            for action in actions:
                await self._society_runtime.publish_event(
                    {
                        "actor_id": self.id,
                        "action": action,
                        "timestamp": datetime.now().isoformat(),
                    }
                )

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _clone_world(self):
        if self._world and hasattr(self._world, "clone"):
            return self._world.clone()
        return None

    def _get_actual_outcome(self, actions: list) -> dict:
        return {
            "actions_executed": len(actions),
            "state_trajectory": [],
            "actual_rewards": [],
        }

    def set_world(self, world: Any) -> None:
        self._world = world
        self._world_view = world

    def set_society_runtime(self, runtime: Any) -> None:
        self._society_runtime = runtime

    async def shutdown(self) -> None:
        self._shutdown = True

    # ── Policy loading (from old ActorSystem) ───────────────────────────────

    def _load_global_policies(self) -> dict:
        try:
            from src.cingulate.governance.policy_registry import (
                PolicyRegistry,
                PolicyCategory,
            )

            registry = PolicyRegistry()
            policies = {}
            for p in registry.get_by_category(PolicyCategory.RUNTIME):
                policies[p.name] = {
                    "enabled": p.enabled,
                    "rules": p.rules,
                    "version": p.version,
                }
            return policies
        except Exception:
            from src.actor.config import GLOBAL_POLICIES

            return dict(GLOBAL_POLICIES)

    def _load_local_policies(self) -> dict:
        try:
            from src.actor.config import LOCAL_POLICIES

            return dict(LOCAL_POLICIES)
        except Exception:
            return {}


# Backward-compatible alias
AutonomousActor = ActorSystem
