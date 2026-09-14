"""Edge Actor — an actor running on an edge node.

STATUS (Cloud/Edge Actor Convergence): standalone tabular-RL prototype,
disconnected from the real Actor Registry/CognitiveActor/governance model
— see src/sync/edge_server.py's module docstring for the full status note
and src/monkey_brain/actor_runtime.py for the real, governed edge
deployment path. Kept unchanged for backward compatibility with its own
existing tests (tests/unit/test_edge_cloud.py).

Layer 2 (Belief Formation) + Layer 3 (Decision) for edge-deployed actors.

Each EdgeActor maintains:
- Local belief (SparseTransitionTensor)
- Local policy (PolicyStore)

EdgeActors sync observations to the cloud and receive world updates.
"""

from __future__ import annotations

import logging

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
from src.monkey_brain.kernel.policy.store import PolicyStore

logger = logging.getLogger("agentos.edge_actor")


class EdgeActor:
    """An actor running on an edge node.

    Maintains local belief and policy. Syncs observations to cloud
    and receives world updates from cloud.

    Layer 2: Belief Formation
        Belief_{a,t} = g(W_t, O_{a,t}, C_a)

    Layer 3: Decision
        Action_{a,t} = π(Belief_{a,t})
    """

    def __init__(self, actor_id: str, node_id: str) -> None:
        self.actor_id = actor_id
        self.node_id = node_id
        self.belief = SparseTransitionTensor()  # Layer 2: local belief
        self.policy = PolicyStore(owner_id=actor_id)  # Layer 3: local policy

    def observe(self, src: str, dst: str, **kwargs) -> None:
        """Observe a transition and fold into local belief.

        Layer 2: Belief Formation
            Belief_{a,t+1} = g(Belief_{a,t}, O_{a,t+1}, C_a)
        """
        self.belief.observe(src, dst, **kwargs)

    def act(self, state: str, legal_actions: list[str] | None = None) -> str:
        """Select action based on local policy.

        Layer 3: Decision
            Action_{a,t} = π(Belief_{a,t})
        """
        if legal_actions is None:
            # Fallback: use all known actions
            legal_actions = list(set(a for (_, a) in self.policy._q.keys()))
        if not legal_actions:
            return ""
        return self.policy.best_action(state, legal_actions)

    def learn(self, state: str, action: str, reward: float, next_state: str = "") -> dict:
        """Learn from experience.

        Layer 4: Learning
            Q_t(s,a) ← Q_t(s,a) + α[r_{t+1} + γ·max Q_t(s',a') - Q_t(s,a)]
        """
        return self.policy.update(state, action, reward, next_state)

    def sync_to_cloud(self) -> dict:
        """Package observations for cloud sync.

        Returns a dict containing:
        - actor_id: which actor
        - node_id: which edge node
        - observations: list of (src, dst) transitions
        - policy_snapshot: Q-values
        """
        observations = []
        for src, dst in self.belief:
            prob = self.belief.feature(src, dst, _PROBABILITY)
            observations.append(
                {
                    "src": src,
                    "dst": dst,
                    "domain": self.belief.domain_of(src),
                    "probability": prob,
                }
            )

        return {
            "actor_id": self.actor_id,
            "node_id": self.node_id,
            "observations": observations,
            "policy_snapshot": self.policy.snapshot(),
        }

    def receive_world_update(self, world_snapshot: dict) -> None:
        """Receive world update from cloud and update local belief.

        Layer 2: Belief Formation
            Belief_{a,t+1} = g(W_{t+1}, O_{a,t+1}, C_a)
        """
        for trans in world_snapshot.get("transitions", []):
            src = trans.get("src", "")
            dst = trans.get("dst", "")
            if src and dst:
                self.belief.observe(
                    src,
                    dst,
                    domain=trans.get("domain", "cloud_sync"),
                )

    def summary(self) -> dict:
        return {
            "actor_id": self.actor_id,
            "node_id": self.node_id,
            "belief_transitions": self.belief.nnz(),
            "policy_q_values": self.policy.size,
        }


# Lazy import to avoid circular dependency
_PROBABILITY = None


def _get_probability_feature():
    global _PROBABILITY
    if _PROBABILITY is None:
        from src.monkey_brain.kernel.compile.tensor import Feature

        _PROBABILITY = Feature.PROBABILITY
    return _PROBABILITY
