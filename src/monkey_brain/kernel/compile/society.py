"""Society — a society of agents over one fixed global world (ADR 0021).

Four-Layer Cognitive Architecture:
  Layer 2: Belief Formation — Each actor has a private belief (local tensor).
  Layer 3: Decision — Each actor has a private policy (PolicyStore).
  The global world is READ-ONLY to actors — no actor can change it.

Trust controls gossip propagation:
  - Actors exchange beliefs only through trusted peers
  - Trust edges define what permissions are granted
  - Gossip respects trust boundaries

What changes is each actor's LOCAL, subjective belief, which grows from:
    1. the actions they take   — asking a question reveals a state's true successors
    2. who they communicate with — peers share what their local beliefs contain

    global state  = the complete transition tensor (fixed; actors only read it)
    local state   = each actor's partial belief (always a subset of global)

Two actors who take different actions and talk to different people end up with
different subjective beliefs — all consistent with (subsets of) the same global
truth. A society of agents representing real people, each with partial knowledge of one
shared world.
"""

from __future__ import annotations

import logging

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.cognitive_actor import (
    CognitiveActor,
    CycleResult,
    Delta,
)
from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor
from src.monkey_brain.kernel.compile.trust import TrustNetwork, Relationship, Perm
from src.monkey_brain.kernel.compile.conflict_resolution import (
    ConflictResolver,
    ResolutionStrategy,
)
from src.monkey_brain.kernel.compile.event_sourcing import (
    EventStore,
    EventType,
    Event,
    get_event_store,
)

logger = logging.getLogger("agentos.compile.society")

# Backward-compatible alias — new code should import CognitiveActor directly
Actor = CognitiveActor


class ActorNetwork:
    """[DEPRECATED] A society: a communication graph over actors, plus the fixed global world.

    ⚠️  PHASE 3 DEPRECATION: Legacy architecture. Use SocietyRuntime instead.
    ActorNetwork will be removed in v1.1. Migrate to src.monkey_brain.kernel.compile.society.SocietyRuntime.

    The network is the ONLY holder of global truth and exposes it read-only. Actors
    learn from it via ask(); they never receive a mutable handle to it.

    Trust controls gossip propagation:
      - Actors exchange beliefs only through trusted peers
      - Trust edges define what permissions are granted
      - Gossip respects trust boundaries
    """

    def __init__(
        self,
        global_world: SparseTransitionTensor,
        trust_network: TrustNetwork | None = None,
        conflict_strategy: ResolutionStrategy = ResolutionStrategy.TRUST_WEIGHTED,
        event_store: EventStore | None = None,
    ) -> None:
        self._global = global_world  # ground truth — never mutated here
        self.actors: dict[str, Actor] = {}
        self._trust = trust_network or TrustNetwork()  # trust controls gossip
        self._conflict_resolver = ConflictResolver(strategy=conflict_strategy)
        self._events = event_store or get_event_store()  # event sourcing

    # ── read-only view of global ─────────────────────────────────────────────────

    def global_transition_count(self) -> int:
        return self._global.nnz()

    def global_states(self) -> list[str]:
        return self._global.states()

    # ── membership / communication graph ─────────────────────────────────────────

    def add(self, actor_id: str | None = None) -> Actor:
        """Add an actor to the network. Generates unique ID if not provided."""
        if not actor_id:
            from uuid import uuid4

            actor_id = f"actor_{uuid4().hex[:8]}"

        actor = self.actors.get(actor_id)
        if actor is None:
            actor = Actor(actor_id)
            self.actors[actor_id] = actor
        return actor

    def connect(
        self,
        a_id: str,
        b_id: str,
        relationship: Relationship = Relationship.COLLEAGUE,
        trust: float | None = None,
        mutual: bool = True,
    ) -> None:
        """Connect two actors for gossip with a trust relationship.

        Creates a trust edge between actors. Gossip propagation respects
        trust permissions — actors only exchange beliefs with trusted peers.
        """
        a, b = self.add(a_id), self.add(b_id)
        a.peers.add(b)
        b.peers.add(a)
        # Create trust edge
        self._trust.connect(a_id, b_id, relationship, trust=trust, mutual=mutual)

    def trust(self, src: str, dst: str) -> float:
        """Get trust score between two actors."""
        return self._trust.trust(src, dst)

    def permits(self, src: str, dst: str, permission: str) -> bool:
        """Check if src has permission to do something with dst."""
        return self._trust.permits(src, dst, permission)

    def grant(self, src: str, dst: str, permission: str) -> bool:
        """Grant a permission to an actor."""
        return self._trust.grant(src, dst, permission)

    def revoke(self, src: str, dst: str, permission: str) -> bool:
        """Revoke a permission from an actor."""
        return self._trust.revoke(src, dst, permission)

    def update_reputation(self, src: str, dst: str, delta: float) -> None:
        """Update reputation based on interaction quality."""
        self._trust.update_reputation(src, dst, delta)

    def resolve_conflicts(self, actor_a: str, actor_b: str) -> list[dict]:
        """Detect and resolve conflicts between two actors' beliefs.

        Returns a list of resolved conflicts with the strategy used.
        """
        a = self.actors.get(actor_a)
        b = self.actors.get(actor_b)
        if not a or not b:
            return []

        trust_score = self._trust.trust(actor_a, actor_b)
        conflicts = self._conflict_resolver.detect_conflicts(a.belief, b.belief, actor_a, actor_b, trust_score)

        resolved = []
        for conflict in conflicts:
            value, strategy = self._conflict_resolver.resolve(conflict, self._trust)
            # Record conflict event
            self._events.append(
                Event(
                    event_type=EventType.CONFLICT,
                    actor_id=actor_a,
                    src=conflict.src,
                    dst=conflict.dst,
                    domain=conflict.domain,
                    old_value=conflict.value_a,
                    new_value=value,
                    metadata={
                        "actor_b": actor_b,
                        "value_a": conflict.value_a,
                        "value_b": conflict.value_b,
                        "strategy": strategy,
                        "severity": conflict.severity,
                    },
                )
            )
            resolved.append(
                {
                    "src": conflict.src,
                    "dst": conflict.dst,
                    "actor_a": conflict.actor_a,
                    "actor_b": conflict.actor_b,
                    "value_a": conflict.value_a,
                    "value_b": conflict.value_b,
                    "resolved_value": value,
                    "strategy": strategy,
                    "severity": conflict.severity,
                }
            )

        return resolved

    def set_conflict_strategy(self, strategy: ResolutionStrategy) -> None:
        """Change the conflict resolution strategy."""
        self._conflict_resolver._strategy = strategy

    def get_event_history(
        self,
        event_type: EventType | None = None,
        actor_id: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query event history with optional filters."""
        events = self._events.query(
            event_type=event_type,
            actor_id=actor_id,
            since=since,
            limit=limit,
        )
        return [e.to_dict() for e in events]

    def get_actor_events(self, actor_id: str, limit: int = 100) -> list[dict]:
        """Get all events for a specific actor."""
        return self.get_event_history(actor_id=actor_id, limit=limit)

    def get_world_events(self, limit: int = 100) -> list[dict]:
        """Get all world transition events."""
        return self.get_event_history(event_type=EventType.TRANSITION, limit=limit)

    # ── action: ask a question (reads global, writes local, propagates) ──────────

    def ask(self, actor_id: str, question: str) -> list[Delta]:
        """The actor asks about a state. The global world (read-only) answers with the
        state's true successors; each becomes a fact in the actor's LOCAL view and is
        propagated to peers. Global is never modified."""
        actor = self.add(actor_id)
        actor.actions.record(f"ask:{question}", question, domain=self._global.domain_of(question))

        learned: list[Delta] = []
        for dst, dst_domain in self._global.successors(question):
            delta = Delta(
                origin=actor_id,
                domain=self._global.domain_of(question),
                src=question,
                dst=dst,
                reward=self._global.feature(question, dst, Feature.REWARD),
                confidence=self._global.feature(question, dst, Feature.CONFIDENCE),
            )
            if actor._learn(delta):
                learned.append(delta)
                # Record event
                self._events.append(
                    Event(
                        event_type=EventType.BELIEF,
                        actor_id=actor_id,
                        src=question,
                        dst=dst,
                        domain=self._global.domain_of(question),
                        feature="probability",
                        new_value=self._global.feature(question, dst, Feature.PROBABILITY),
                    )
                )
                self._propagate(actor, delta)
        logger.info(
            "[society] %s asked %r → learned %d transition(s), coverage=%.2f",
            actor_id,
            question,
            len(learned),
            self.coverage(actor_id),
        )
        _obs.counter("society.ask")
        _obs.gauge("society.coverage", self.coverage(actor_id), actor=actor_id)
        return learned

    def _propagate(self, origin: Actor, delta: Delta) -> int:
        """Gossip a learned fact through the origin's connected component.

        Only local views are written; global is untouched.
        Trust permissions control propagation — actors only exchange beliefs
        with peers who have the appropriate permissions.
        """
        reached = 0
        frontier = [origin]
        visited = {origin.id}
        while frontier:
            cur = frontier.pop()
            for peer in cur.peers:
                if peer.id in visited:
                    continue
                # Check trust permissions before gossiping
                if not self._trust.permits(cur.id, peer.id, Perm.PUBLISH_OBSERVATIONS):
                    continue
                if not self._trust.permits(peer.id, cur.id, Perm.SUBSCRIBE_OBSERVATIONS):
                    continue
                if peer._learn(delta):
                    reached += 1
                    visited.add(peer.id)
                    # Record gossip event
                    self._events.append(
                        Event(
                            event_type=EventType.GOSSIP,
                            actor_id=cur.id,
                            src=delta.src,
                            dst=delta.dst,
                            domain=delta.domain,
                            metadata={
                                "target_actor": peer.id,
                                "origin_actor": delta.origin,
                            },
                        )
                    )
                    frontier.append(peer)
        return reached

    # ── introspection: local vs global ──────────────────────────────────────────

    def local(self, actor_id: str) -> SparseTransitionTensor:
        return self.actors[actor_id].world

    def coverage(self, actor_id: str) -> float:
        """Fraction of the global world this actor's local view has discovered."""
        g = set(self._global)
        if not g:
            return 1.0
        local = set(self.actors[actor_id].world)
        return len(local & g) / len(g)

    def divergence(self, actor_id: str) -> int:
        """Count of global transitions the actor does not yet know (the local/global gap)."""
        local = set(self.actors[actor_id].world)
        return sum(1 for edge in self._global if edge not in local)

    def summary(self) -> dict:
        return {
            "actors": len(self.actors),
            "global_transitions": self._global.nnz(),  # fixed
            "links": sum(len(a.peers) for a in self.actors.values()) // 2,
            "coverage": {aid: round(self.coverage(aid), 3) for aid in sorted(self.actors)},
            "trust_edges": len(self._trust._edges),
            "conflicts": self._conflict_resolver.summary(),
        }
