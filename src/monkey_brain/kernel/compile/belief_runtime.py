"""BeliefRuntime — Belief formation kernel injected into each actor.

Manages Layer 2: Belief Formation
  Belief_a = g(W_global, O_a, C_a)

Owns complete belief lifecycle:
  1. Observation Queue — Layer 1 → 2 handoff
  2. Observation Caching — track recent observations
  3. Conflict Detection — find contradictions
  4. Belief Fusion — integrate with trust weighting
  5. Belief Versioning — track evolution over time
  6. Consolidation — conflict resolution strategy
  7. Introspection — query belief state

Decoupled from trust and cognition so belief can be tested independently.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.context import Context
from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor
from src.monkey_brain.kernel.compile.trust_runtime import TrustRuntime

logger = logging.getLogger("agentos.compile.belief_runtime")


class ConsolidationStrategy(Enum):
    """Conflict resolution strategies for belief fusion."""

    ACCEPT_ALL = "accept_all"  # fuse all observations
    TRUST_WEIGHTED = "trust_weighted"  # weight by trust (default)
    QUARANTINE = "quarantine"  # skip conflicting observations
    MAJORITY_VOTE = "majority_vote"  # only high-agreement edges


@dataclass
class BeliefConflict:
    """Conflict between observation and current belief."""

    edge: tuple[str, str]  # (src, dst)
    local_reward: float
    proposed_reward: float
    delta: float
    confidence: float
    origin: str


class BeliefRuntime:
    """Belief formation kernel — complete Layer 2 lifecycle.

    Injected INTO each ActorRuntime. Decoupled from trust and cognition.

    Owns:
      - local belief tensor (actor's private world model)
      - observation queue (from Layer 1: Knowledge Exchange)
      - observation cache (recent observations, metadata)
      - conflict detection and resolution
      - belief versioning (track evolution)
      - trust_runtime (for weighting observations)
    """

    def __init__(
        self,
        local_belief: SparseTransitionTensor,
        context: Context,
        trust_runtime: TrustRuntime | None = None,
        consolidation_strategy: ConsolidationStrategy | None = None,
    ) -> None:
        self.belief = local_belief
        self.context = context
        self._trust = trust_runtime or TrustRuntime()
        self._strategy = consolidation_strategy or ConsolidationStrategy.TRUST_WEIGHTED

        # Layer 1 → Layer 2 handoff: observation queue from exchange
        self._observation_queue: list[dict] = []

        # 2. Observation caching: track recent observations
        self._recent_observations: dict[tuple, dict] = {}  # key → observation
        self._observation_history: list[dict] = []  # chronological log

        # 5. Belief versioning: track evolution
        self._belief_version = 0
        self._belief_snapshots: dict[int, Any] = {}  # version → checkpoint

        # 3. Conflict tracking
        self._recent_conflicts: list[BeliefConflict] = []

    @property
    def _actor_id(self) -> str:
        return self.context.actor_id if hasattr(self.context, "actor_id") else "unknown"

    # ── 1. Observation Queue: Layer 1 → Layer 2 handoff ────────────

    def accept_proposal(self, proposal: Any) -> None:
        """LAYER 1 → LAYER 2 handoff: Queue proposal for belief fusion.

        Called by KnowledgeExchange.deliver() after validation.
        At this point: signature ✓, identity ✓, policy ✓, trust_score ≥ threshold ✓

        But trust weighting has NOT been applied yet — that happens in fuse_observations().
        """
        observation = {
            "origin": proposal.origin,
            "transitions": list(proposal.transitions),
            "domain": proposal.domain,
            "kind": proposal.kind,
            "queued_at": time.time(),
        }
        self._observation_queue.append(observation)
        _obs.gauge(
            "belief.observation_queue_size",
            float(len(self._observation_queue)),
            actor=self._actor_id,
        )
        logger.debug(
            "[belief_runtime] %s queued proposal from %s (%d transitions)",
            self._actor_id,
            proposal.origin,
            len(proposal.transitions),
        )

    # ── 2. Observation Caching ─────────────────────────────────────

    def _cache_observation(self, observation: dict) -> None:
        """Cache observation for conflict detection and auditing."""
        key = (observation["origin"], tuple(observation["transitions"]))
        self._recent_observations[key] = observation
        self._observation_history.append({**observation, "cached_at": time.time()})

        # Keep cache bounded
        if len(self._recent_observations) > 1000:
            oldest_key = next(iter(self._recent_observations))
            del self._recent_observations[oldest_key]

        # Keep history bounded (10k entries)
        if len(self._observation_history) > 10000:
            self._observation_history = self._observation_history[-10000:]

    # ── 3. Conflict Detection ──────────────────────────────────────

    def detect_conflicts(self, observation: dict) -> list[BeliefConflict]:
        """Find edges where observation contradicts current belief.

        Returns list of BeliefConflict objects.
        """
        conflicts: list[BeliefConflict] = []

        for src, dst, reward in observation["transitions"]:
            if self.belief.has_edge(src, dst):
                local_reward = self.belief.feature(src, dst, Feature.REWARD)
                proposed_reward = float(reward) if reward is not None else 0.0
                delta = abs(local_reward - proposed_reward)

                if delta > 1e-6:  # non-trivial difference
                    conflicts.append(
                        BeliefConflict(
                            edge=(src, dst),
                            local_reward=local_reward,
                            proposed_reward=proposed_reward,
                            delta=delta,
                            confidence=observation.get("confidence", 0.5),
                            origin=observation["origin"],
                        )
                    )

        return conflicts

    # ── 4. Belief Fusion ──────────────────────────────────────────

    def fuse_observations(self, world_view: Any | None = None) -> dict:
        """LAYER 2: Belief Formation — g(W_global, O_a, C_a)

        Fuse observations into local belief using trust weighting and consolidation strategy.

        Inputs:
          W_global = world_view (read-only global world)
          O_a      = self._observation_queue (accepted proposals)
          C_a      = self.context (actor's context: tenant, epoch, etc.)

        Process (depends on consolidation_strategy):
          - ACCEPT_ALL: integrate all observations
          - TRUST_WEIGHTED: weight by trust (default)
          - QUARANTINE: skip observations with conflicts
          - MAJORITY_VOTE: only fuse high-agreement edges

        Output:
          Updated belief Belief_a = g(W_global, O_a, C_a)
        """
        if not self._observation_queue:
            return {"fused_count": 0, "total_weight": 0.0, "conflicts": 0}

        # Use consolidation strategy
        if self._strategy == ConsolidationStrategy.ACCEPT_ALL:
            result = self._fuse_accept_all()
        elif self._strategy == ConsolidationStrategy.TRUST_WEIGHTED:
            result = self._fuse_trust_weighted()
        elif self._strategy == ConsolidationStrategy.QUARANTINE:
            result = self._fuse_quarantine()
        elif self._strategy == ConsolidationStrategy.MAJORITY_VOTE:
            result = self._fuse_majority_vote()
        else:
            result = self._fuse_trust_weighted()

        # Clear queue after fusion
        self._observation_queue.clear()

        # Update belief version
        self._belief_version += 1

        logger.info(
            "[belief_runtime] %s fused %d obs, %d conflicts (weight=%.2f, v=%d, nnz=%d)",
            self._actor_id,
            result["fused_count"],
            result["conflicts"],
            result["total_weight"],
            self._belief_version,
            self.belief.nnz(),
        )
        _obs.event(
            "belief.fusion",
            actor=self._actor_id,
            fused_count=result["fused_count"],
            conflicts=result["conflicts"],
            total_weight=round(result["total_weight"], 3),
            belief_version=self._belief_version,
        )
        _obs.gauge("belief.nnz", float(self.belief.nnz()), actor=self._actor_id)

        return {
            "fused_count": result["fused_count"],
            "total_weight": result["total_weight"],
            "conflicts": result["conflicts"],
            "belief_nnz": self.belief.nnz(),
            "belief_version": self._belief_version,
        }

    def _fuse_trust_weighted(self) -> dict:
        """Fuse all observations, weighted by trust."""
        return self._fuse_loop(weight_mode="trust", skip_on_conflict=False, record_conflicts=True)

    def _fuse_accept_all(self) -> dict:
        """Fuse all observations with base weight 1.0."""
        return self._fuse_loop(weight_mode="fixed", skip_on_conflict=False, record_conflicts=False)

    def _fuse_quarantine(self) -> dict:
        """Skip observations with conflicts."""
        return self._fuse_loop(weight_mode="trust", skip_on_conflict=True, record_conflicts=True)

    def _fuse_loop(
        self,
        *,
        weight_mode: str = "trust",
        skip_on_conflict: bool = False,
        record_conflicts: bool = True,
    ) -> dict:
        """Common fusion loop shared by all strategies.

        Args:
            weight_mode: "trust" uses trust-weighted weight, "fixed" uses 1.0
            skip_on_conflict: if True, skip entire observation when conflicts found
            record_conflicts: if True, append conflicts to _recent_conflicts
        """
        fused_count = 0
        total_weight = 0.0
        conflicts_count = 0

        for observation in self._observation_queue:
            origin = observation.get("origin", "unknown")
            transitions = observation.get("transitions", [])
            domain = observation.get("domain", "default")

            conflicts = self.detect_conflicts(observation)
            if conflicts:
                conflicts_count += len(conflicts)
                if record_conflicts:
                    self._recent_conflicts.extend(conflicts)
                    # Keep conflicts bounded (5k entries)
                    if len(self._recent_conflicts) > 5000:
                        self._recent_conflicts = self._recent_conflicts[-5000:]
                if skip_on_conflict:
                    logger.warning(
                        "[belief_runtime] quarantine: %d conflicts from %s",
                        len(conflicts),
                        origin,
                    )
                    continue

            for src, dst, reward in transitions:
                if weight_mode == "trust":
                    weight = self._trust.weight_observation(1.0, origin, self._actor_id)
                else:
                    weight = 1.0
                self.belief.observe(
                    src,
                    dst,
                    domain=domain,
                    reward=float(reward) if reward else 0.0,
                    weight=weight,
                )
                fused_count += 1
                total_weight += weight

            self._cache_observation(observation)

        return {
            "fused_count": fused_count,
            "total_weight": total_weight,
            "conflicts": conflicts_count,
        }

    def _fuse_majority_vote(self) -> dict:
        """Only fuse edges with high agreement across observations."""
        # Collect votes on transitions
        transition_votes: dict[tuple, list[float]] = {}

        for observation in self._observation_queue:
            for src, dst, reward in observation["transitions"]:
                key = (src, dst)
                if key not in transition_votes:
                    transition_votes[key] = []
                transition_votes[key].append(float(reward) if reward else 0.0)

        # Fuse high-agreement edges
        fused_count = 0
        total_weight = 0.0

        for (src, dst), rewards in transition_votes.items():
            avg_reward = sum(rewards) / len(rewards)
            # Simple agreement metric: variance-based
            agreement = max(1.0 - (self._variance(rewards) / (1.0 + abs(avg_reward))), 0.0)

            if agreement > 0.5:  # > 50% agreement
                self.belief.observe(src, dst, reward=avg_reward, weight=agreement)
                fused_count += 1
                total_weight += agreement

        for obs in self._observation_queue:
            self._cache_observation(obs)

        skipped = len(transition_votes) - fused_count
        return {
            "fused_count": fused_count,
            "total_weight": total_weight,
            "conflicts": skipped,
        }

    @staticmethod
    def _variance(values: list[float]) -> float:
        """Compute variance of values."""
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)

    # ── 5. Belief Versioning ──────────────────────────────────────

    def belief_version(self) -> int:
        """Current belief version (incremented on each fusion)."""
        return self._belief_version

    def checkpoint_belief(self) -> int:
        """Save belief snapshot and return version number."""
        version = self._belief_version
        try:
            self._belief_snapshots[version] = self.belief.save()
        except Exception as e:
            logger.debug("[belief_runtime] checkpoint failed: %s", e)
        return version

    def belief_at_version(self, version: int) -> Any:
        """Restore belief state from version (or None if not found)."""
        return self._belief_snapshots.get(version)

    # ── 6. Persistence (checkpoint / restore) ───────────────────────
    #
    # NOT the belief tensor itself — ActorRuntime.checkpoint()/restore()
    # persists that separately via self.belief.save()/.load(). This
    # persists the belief-FORMATION bookkeeping this class actually owns:
    # version, consolidation strategy, observation history, conflicts.
    # ActorRuntime.checkpoint()/restore() call these via hasattr guards
    # that were always False before these methods existed.
    #
    # Step 14 — Architecture Consolidation: this BeliefRuntime (Layer 2
    # Knowledge Exchange fusion kernel) is dormant on the live /prompt tick
    # path — see ActorRuntime.checkpoint()'s docstring. These methods stay
    # correct for the legacy ActorRuntime.checkpoint()/restore() bundle;
    # /prompt's belief-restore path persists kernel/pipeline/belief_state.py::BeliefState
    # instead, via PlanetaryRuntime.restore_actor_belief()/checkpoint_actor_belief().

    def checkpoint(self, path: str) -> None:
        """Persist belief-formation state so restore() can rebuild it."""
        import json
        import os
        from pathlib import Path

        payload = {
            "belief_version": self._belief_version,
            "consolidation_strategy": self._strategy.value,
            "observation_history": self._observation_history[-1000:],
            "recent_conflicts": [
                {
                    "edge": list(c.edge),
                    "local_reward": c.local_reward,
                    "proposed_reward": c.proposed_reward,
                    "delta": c.delta,
                    "confidence": c.confidence,
                    "origin": c.origin,
                }
                for c in self._recent_conflicts[-1000:]
            ],
        }
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.tmp.{os.getpid()}")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                tmp.unlink()
        logger.info(
            "[belief_runtime] %s checkpointed belief runtime (v%d) -> %s",
            self._actor_id,
            self._belief_version,
            p,
        )

    def restore(self, path: str) -> None:
        """Rebuild belief-formation state from a checkpoint written by
        checkpoint(). No-op (fresh state stands) when no checkpoint exists
        yet — e.g. an actor's first-ever request has nothing to restore."""
        import json
        from pathlib import Path

        p = Path(path)
        if not p.exists():
            logger.debug(
                "[belief_runtime] %s no checkpoint at %s — starting fresh",
                self._actor_id,
                p,
            )
            return
        try:
            payload = json.loads(p.read_text())
        except Exception as e:
            logger.warning(
                "[belief_runtime] %s checkpoint at %s unreadable, starting fresh: %s",
                self._actor_id,
                p,
                e,
            )
            return

        self._belief_version = payload.get("belief_version", 0)
        strategy_value = payload.get("consolidation_strategy")
        if strategy_value:
            try:
                self._strategy = ConsolidationStrategy(strategy_value)
            except ValueError:
                pass
        self._observation_history = payload.get("observation_history", [])
        self._recent_conflicts = [
            BeliefConflict(
                edge=tuple(c["edge"]),
                local_reward=c["local_reward"],
                proposed_reward=c["proposed_reward"],
                delta=c["delta"],
                confidence=c["confidence"],
                origin=c["origin"],
            )
            for c in payload.get("recent_conflicts", [])
        ]
        logger.info(
            "[belief_runtime] %s restored belief runtime (v%d) <- %s",
            self._actor_id,
            self._belief_version,
            p,
        )

    # ── 7. Introspection ───────────────────────────────────────────

    def observation_queue_size(self) -> int:
        """Number of pending observations."""
        return len(self._observation_queue)

    def belief_size(self) -> int:
        """Number of transitions in belief."""
        return self.belief.nnz()

    def belief_sources(self) -> dict[str, int]:
        """How many observations from each origin."""
        sources: dict[str, int] = {}
        for obs in self._observation_history:
            origin = obs.get("origin", "unknown")
            sources[origin] = sources.get(origin, 0) + 1
        return sources

    def belief_coverage(self, world: Any) -> float:
        """What fraction of world does actor know? [0, 1]"""
        try:
            known = set((s, d) for s, d in self.belief)
            world_edges = set((s, d) for s, d in world)
            if not world_edges:
                return 1.0
            return len(known & world_edges) / len(world_edges)
        except Exception:
            return 0.0

    def conflicting_observations(self, limit: int = 10) -> list[dict]:
        """Recent observations that had conflicts."""
        conflicts_by_obs = {}
        for conflict in self._recent_conflicts[-limit * 5 :]:
            obs_key = conflict.origin
            if obs_key not in conflicts_by_obs:
                conflicts_by_obs[obs_key] = []
            conflicts_by_obs[obs_key].append(
                {
                    "edge": conflict.edge,
                    "local": conflict.local_reward,
                    "proposed": conflict.proposed_reward,
                    "delta": conflict.delta,
                }
            )
        return [{"origin": origin, "conflicts": confs} for origin, confs in list(conflicts_by_obs.items())[-limit:]]

    def recent_outcome_history(self, limit: int = 10) -> list[dict]:
        """Recent observations from history."""
        return [
            {
                "origin": obs["origin"],
                "transitions": len(obs["transitions"]),
                "domain": obs["domain"],
                "queued_at": obs.get("queued_at", 0),
            }
            for obs in self._observation_history[-limit:]
        ]

    def summary(self) -> dict[str, Any]:
        """Belief runtime summary for introspection."""
        return {
            "type": "BeliefRuntime",
            "actor": self._actor_id,
            "belief_transitions": self.belief.nnz(),
            "belief_version": self._belief_version,
            "queued_observations": len(self._observation_queue),
            "cached_observations": len(self._recent_observations),
            "observation_sources": self.belief_sources(),
            "recent_conflicts": len(self._recent_conflicts),
            "consolidation_strategy": self._strategy.value,
            "trust_runtime": self._trust.summary(),
        }
