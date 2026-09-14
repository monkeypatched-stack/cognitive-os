"""Actor — identity-only entity in the cognitive pipeline.

An Actor encapsulates identity and lifecycle.
It does NOT own BeliefState, policy, or memory — those belong to the runtime.

ARCHITECTURAL SEPARATION (ACP-2):

Actor supplies to Runtime:
    identity     — who am I (actor_id, tenant_id)
    capabilities — what I can do
    goals        — what I want to achieve
    local memory — what I remember

Runtime owns (NOT the actor):
    belief       — what do I know (BeliefState)
    policy       — what should I do (PolicyStore)
    Φ            — compiled sparse transition operator
    trust        — who do I believe
    execution    — what am I doing now
    planning     — deciding what to do
    learning     — improving from outcomes

Why this separation?
    Actor A → Edge Device
    Actor A → Cloud Replica

    Both reasoning about the same actor. If BeliefState lives inside Actor,
    replication becomes painful. Instead:

    Actor → ActorId (identity)
    BeliefStore → ActorId → BeliefState (cognition)

    Cognition moves independently of actor instances.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Actor:
    """Identity-only entity. Does NOT own cognition.

    Owns:
    - identity (actor_id, tenant_id)
    - lifecycle state
    - trust score
    - metadata

    Does NOT own:
    - BeliefState (that's the runtime's BeliefStore)
    - Policy (that's the runtime's Φ compilation)
    - Memory (that's the runtime's memory store)
    """

    actor_id: str = ""
    tenant_id: str = "default"
    trust_score: float = 0.5
    status: str = "idle"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_reasoned_at: float = 0.0
    cycle_count: int = 0

    def start_reasoning(self) -> None:
        self.status = "reasoning"

    def finish_reasoning(self) -> None:
        self.status = "idle"
        self.last_reasoned_at = time.time()
        self.cycle_count += 1

    def block(self, reason: str = "") -> None:
        self.status = f"blocked:{reason}"

    def terminate(self) -> None:
        self.status = "terminated"

    def is_active(self) -> bool:
        return self.status == "idle"

    def snapshot(self) -> ActorSnapshot:
        return ActorSnapshot(
            actor_id=self.actor_id,
            tenant_id=self.tenant_id,
            trust_score=self.trust_score,
            status=self.status,
            cycle_count=self.cycle_count,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "trust_score": round(self.trust_score, 3),
            "cycle_count": self.cycle_count,
        }


@dataclass(frozen=True)
class ActorSnapshot:
    """Immutable snapshot of an actor's identity at a point in time."""

    actor_id: str = ""
    tenant_id: str = ""
    trust_score: float = 0.0
    status: str = ""
    cycle_count: int = 0
