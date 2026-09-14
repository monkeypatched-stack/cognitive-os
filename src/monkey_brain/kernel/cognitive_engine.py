"""CognitionEngine — unified wiring of all cognitive components.

The world tensor is a CONSENSUS BELIEF — not a consensus of policies,
not a consensus of rewards, but a consensus of OBSERVATIONS.

Mathematically:

    W = f(O₁, O₂, ..., Oₙ)

where:
    Oᵢ = actor i's observations
    f  = WorldLearner (fusion function)
    W  = consensus world model

Every actor contributes evidence.
The WorldLearner fuses the evidence into a shared belief about reality.

Architecture:

    Actor A observes → O₁ ─┐
    Actor B observes → O₂ ─┼──→ WorldLearner f(·) ──→ W (consensus)
    Actor C observes → O₃ ─┘                              │
                                                           │
                    ┌──────────────────────────────────────┘
                    ▼
              W (consensus belief)
                    │
         ┌──────────┼──────────┐
         ▼          ▼          ▼
    Actor A    Actor B    Actor C
    Belief A   Belief B   Belief C
    (local)    (local)    (local)
         │          │          │
         ▼          ▼          ▼
    Policy A   Policy B   Policy C
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
from src.monkey_brain.kernel.compile.action_operator import (
    ActionOperator,
    ActionLegality,
)
from src.monkey_brain.kernel.learn.world_learner import WorldLearner
from src.monkey_brain.kernel.learn.policy_learner import PolicyLearner
from src.monkey_brain.kernel.policy.store import PolicyStore

logger = logging.getLogger("agentos.cognition_engine")


class CognitionEngine:
    """Unified wiring of all cognitive components.

    Owns:
        - Shared World Tensor (read-only to actors)
        - WorldLearner (sole authority for world updates)
        - Per-actor PolicyStores
        - Per-actor PolicyLearners
        - Action operators and legality computation

    Usage:
        engine = CognitionEngine(0)

        # Actors propose observations
        engine.propose_observation("s1", "s2", origin="actor_a")

        # WorldLearner decides
        engine.commit_observations()

        # Actors get beliefs and policies
        actor_a = engine.create_actor("actor_a")
        actor_b = engine.create_actor("actor_b")

        # Compute legal actions
        legal = engine.legal_actions("s1", actor_id="actor_a")
    """

    def __init__(self, engine_id: int = 0) -> None:
        self.engine_id = engine_id
        # Shared world tensor (read-only to actors)
        self._world = SparseTransitionTensor()

        # WorldLearner: sole authority for world updates
        self._world_learner = WorldLearner(self._world)

        # Per-actor components
        self._actors: dict[str, dict] = {}  # actor_id → {belief, policy, policy_learner}

        # Action operators and legality
        self._action_operators: dict[str, ActionOperator] = {}
        self._legality = ActionLegality(self._world)

        logger.info(
            "[cognition_engine:%d] initialized with %d states, %d transitions",
            self.engine_id,
            len(self._world.states()),
            self._world.nnz(),
        )

    # ── World (shared, read-only to actors) ────────────────────────────────

    @property
    def world(self) -> SparseTransitionTensor:
        """The shared world tensor. Read-only to actors."""
        return self._world

    @property
    def world_learner(self) -> WorldLearner:
        """The WorldLearner. Sole authority for world updates."""
        return self._world_learner

    # ── Observation proposals ───────────────────────────────────────────────

    def propose_observation(
        self,
        src: str,
        dst: str,
        *,
        domain: str = "default",
        dst_domain: str | None = None,
        latency_ms: float = 0.0,
        cost: float = 0.0,
        confidence: float = 0.0,
        origin: str = "",
        weight: float = 1.0,
    ) -> bool:
        """Propose an observation to the WorldLearner.

        Returns True if accepted, False if rejected.
        """
        return self._world_learner.observe_transition(
            src,
            dst,
            domain=domain,
            dst_domain=dst_domain,
            latency_ms=latency_ms,
            cost=cost,
            confidence=confidence,
            origin=origin,
            weight=weight,
        )

    def propose_batch(self, transitions: list[dict]) -> int:
        """Propose a batch of observations. Returns count accepted."""
        return self._world_learner.batch_observe(transitions)

    # ── Actors (belief + policy per actor) ─────────────────────────────────

    def create_actor(self, actor_id: str) -> dict:
        """Create an actor with local belief and policy.

        Returns dict with:
            - belief: local SparseTransitionTensor (subset of world)
            - policy: PolicyStore (per-actor Q-values)
            - policy_learner: PolicyLearner (updates policy)
        """
        if actor_id in self._actors:
            return self._actors[actor_id]

        belief = SparseTransitionTensor()
        policy = PolicyStore()
        policy_learner = PolicyLearner(policy)

        actor = {
            "id": actor_id,
            "belief": belief,
            "policy": policy,
            "policy_learner": policy_learner,
        }
        self._actors[actor_id] = actor

        logger.info("[cognitive_arch] created actor %s", actor_id)
        return actor

    def get_actor(self, actor_id: str) -> dict | None:
        """Get an existing actor."""
        return self._actors.get(actor_id)

    def actor_observe(self, actor_id: str, src: str, dst: str, **kwargs) -> None:
        """Actor observes a transition and folds it into its local belief."""
        actor = self._actors.get(actor_id)
        if actor is None:
            actor = self.create_actor(actor_id)
        actor["belief"].observe(src, dst, **kwargs)

    def actor_learn(
        self,
        actor_id: str,
        state: str,
        action: str,
        reward: float,
        next_state: str = "",
        actor_loss: float = 0.0,
    ) -> dict:
        """Actor learns from experience. Updates its local policy."""
        actor = self._actors.get(actor_id)
        if actor is None:
            actor = self.create_actor(actor_id)
        return actor["policy_learner"].update(state, action, reward, next_state, actor_loss)

    # ── Action operators ───────────────────────────────────────────────────

    def register_action(self, action_name: str) -> ActionOperator:
        """Register an action operator over the shared world tensor."""
        op = ActionOperator(action_name, self._world)
        self._action_operators[action_name] = op
        return op

    def get_action(self, action_name: str) -> ActionOperator | None:
        """Get a registered action operator."""
        return self._action_operators.get(action_name)

    def legal_actions(
        self,
        state: str,
        actor_id: str | None = None,
        capabilities: set[str] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> list[str]:
        """Compute legal actions for a state.

        If actor_id is provided, filter by the actor's capabilities.
        """
        caps = capabilities
        if actor_id and caps is None:
            # Actor's capabilities are the actions it has registered
            caps = set(self._action_operators.keys())

        return self._legality.compute(
            state,
            capabilities=caps,
            constraints=constraints,
            action_operators=self._action_operators,
        )

    # ── Summary ────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "engine_id": self.engine_id,
            "world_states": len(self._world.states()),
            "world_transitions": self._world.nnz(),
            "world_learner": self._world_learner.summary(),
            "actors": len(self._actors),
            "actor_ids": list(self._actors.keys()),
            "action_operators": len(self._action_operators),
            "action_names": list(self._action_operators.keys()),
        }


# backward-compatible alias
CognitiveArchitecture = CognitionEngine
