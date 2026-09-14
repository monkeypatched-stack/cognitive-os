"""TrustRuntime — Trust fabric kernel injected into each actor.

Owns the complete trust management pipeline:
  1. Trust Graph — the network structure
  2. Trust Queries — looking up trust relationships
  3. Trust Propagation — spreading trust through the network
  4. Trust Decay — time-based degradation
  5. Trust Learning — updating trust from outcomes
  6. Observation Weighting — applying trust to observations

Injected INTO each ActorRuntime. Decoupled from belief formation.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.trust import TrustNetwork

logger = logging.getLogger("agentos.compile.trust_runtime")


class TrustRuntime:
    """Trust fabric kernel — manages complete trust lifecycle.

    Injected INTO each ActorRuntime. Owns:
      1. Trust Graph (TrustNetwork)
      2. Trust Queries (lookups with propagation)
      3. Trust Propagation (multi-hop paths)
      4. Trust Decay (time-based degradation)
      5. Trust Learning (update from outcomes)
      6. Observation Weighting (apply trust to weights)
    """

    def __init__(self, network: TrustNetwork | None = None, actor_id: str = "unknown") -> None:
        self._network = network or TrustNetwork()
        self._actor_id = actor_id

        # Trust decay tracking: (origin, recipient) → timestamp
        self._last_query: dict[tuple[str, str], float] = {}

        # Trust learning: collect observations for updating scores
        self._outcome_history: list[dict] = []

        # Trust propagation cache to avoid redundant traversals
        self._propagation_cache: dict[tuple[str, str], float] = {}
        self._cache_invalidated = True

    # ── 1. Trust Graph ─────────────────────────────────────────────────────────

    def graph(self) -> TrustNetwork:
        """Access to trust graph for inspection."""
        return self._network

    # ── 2. Trust Queries ───────────────────────────────────────────────────────

    def trust(self, origin: str, recipient: str | None = None) -> float:
        """Query trust: how much does recipient trust origin?

        Returns a float in [0.0, 1.0]:
          1.0 = complete trust
          0.5 = neutral
          0.0 = revoked (no trust)

        Recipient defaults to actor_id if not specified.
        """
        if recipient is None:
            recipient = self._actor_id

        if not self._network:
            return 1.0  # default: neutral trust

        try:
            # Direct query first (single hop)
            score = self._network.trust(origin, recipient)

            # If no trust edge exists, default to neutral (1.0)
            # This ensures observations from unknown origins still influence the world
            if score == 0.0:
                score = 1.0

            score = max(0.0, min(1.0, score))  # clamp to [0, 1]

            # Apply trust decay over time
            score = self._apply_decay(origin, recipient, score)

            # Record query for decay tracking
            self._last_query[(origin, recipient)] = time.time()

            return score
        except Exception as e:
            logger.debug("[trust_runtime] lookup failed %s → %s: %s", origin, recipient, e)
            return 1.0  # default on error

    # ── 3. Trust Propagation ───────────────────────────────────────────────────

    def trust_via_chain(self, origin: str, recipient: str | None = None, max_hops: int = 3) -> float:
        """Multi-hop trust propagation through the network.

        If no direct trust edge exists, find the best path through intermediaries.

        Example:
          trust(alice → bob) = 0.8 (direct)
          trust(alice → charlie → bob) = 0.9 * 0.8 = 0.72 (via charlie)
          → return max(0.8, 0.72) = 0.8
        """
        if recipient is None:
            recipient = self._actor_id

        # Check direct score first
        direct = self.trust(origin, recipient)

        if direct >= 0.99:  # practically certain
            return direct

        # Try multi-hop paths (BFS through trust graph)
        try:
            best_path_score = self._find_best_trust_path(origin, recipient, max_hops)
            return max(direct, best_path_score)
        except Exception as e:
            logger.debug("[trust_runtime] propagation failed %s → %s: %s", origin, recipient, e)
            return direct

    def _find_best_trust_path(self, origin: str, recipient: str, max_hops: int) -> float:
        """Find highest-trust path from origin to recipient (BFS)."""
        from collections import deque

        queue = deque([(origin, 1.0, 0)])  # (current, path_score, hops)
        visited = {origin}
        best_score = 0.0

        while queue:
            current, score, hops = queue.popleft()

            if current == recipient:
                best_score = max(best_score, score)
                continue

            if hops >= max_hops:
                continue

            # Explore neighbors (find outgoing edges from current)
            try:
                if hasattr(self._network, "_edges"):
                    for (src, dst), edge in self._network._edges.items():
                        if src == current and dst not in visited:
                            edge_score = edge.trust_score if edge.is_active else 0.0
                            path_score = score * edge_score
                            if path_score > best_score:
                                visited.add(dst)
                                queue.append((dst, path_score, hops + 1))
            except Exception:
                logger.debug("_find_best_trust_path: suppressed exception", exc_info=True)

        return best_score

    # ── 4. Trust Decay ────────────────────────────────────────────────────────

    def _apply_decay(
        self,
        origin: str,
        recipient: str,
        score: float,
        decay_rate: float = 0.01,
        half_life_days: float = 30,
    ) -> float:
        """Apply time-based trust decay.

        Trust decreases over time if not reinforced.
        half_life_days: days until score decays to 50%
        """
        last_query_time = self._last_query.get((origin, recipient), time.time())
        elapsed_seconds = time.time() - last_query_time
        elapsed_days = elapsed_seconds / (24 * 3600)

        # Exponential decay: score' = score * 2^(-elapsed_days / half_life_days)
        decay_factor = 2.0 ** (-elapsed_days / half_life_days)
        decayed_score = score * decay_factor

        return max(0.0, decayed_score)

    # ── 5. Trust Learning ──────────────────────────────────────────────────────

    def learn_from_outcome(self, origin: str, recipient: str | None = None, outcome: dict | None = None) -> None:
        """Update trust based on observation outcome.

        Outcomes: good (trust increases), bad (trust decreases), neutral (no change).

        outcome = {
            "status": "good" | "bad" | "neutral",
            "confidence": 0.0-1.0,
            "reason": "str",
        }
        """
        if recipient is None:
            recipient = self._actor_id

        if outcome is None:
            outcome = {"status": "neutral", "confidence": 0.5}

        # Record learning event
        event = {
            "timestamp": time.time(),
            "origin": origin,
            "recipient": recipient,
            "outcome": outcome,
        }
        self._outcome_history.append(event)

        # Update trust score based on outcome
        current_score = self._network.trust(origin, recipient)
        adjustment = 0.0

        if outcome.get("status") == "good":
            confidence = outcome.get("confidence", 0.5)
            adjustment = 0.1 * confidence  # increase by up to 10%
        elif outcome.get("status") == "bad":
            confidence = outcome.get("confidence", 0.5)
            adjustment = -0.2 * confidence  # decrease by up to 20%

        new_score = max(0.0, min(1.0, current_score + adjustment))

        try:
            # Update the trust network with new score using reputation update
            adjustment = new_score - current_score
            self._network.update_reputation(origin, recipient, adjustment)
            self._cache_invalidated = True

            logger.info(
                "[trust_runtime] learned: %s → %s outcome=%s (%.2f → %.2f)",
                origin,
                recipient,
                outcome.get("status"),
                current_score,
                new_score,
            )
            _obs.event(
                "trust.learn",
                origin=origin,
                recipient=recipient,
                status=outcome.get("status"),
                old_score=round(current_score, 3),
                new_score=round(new_score, 3),
            )
        except Exception as e:
            logger.debug("[trust_runtime] learning failed: %s", e)

    # ── 6. Observation Weighting ───────────────────────────────────────────────

    def weight_observation(self, base_weight: float, origin: str, recipient: str | None = None) -> float:
        """Compute effective weight for an observation.

        effective_weight = base_weight * trust(origin, recipient)

        Higher trust → higher weight in frequency updates
        Lower trust → lower weight (graceful degradation)
        """
        if recipient is None:
            recipient = self._actor_id

        trust_score = self.trust(origin, recipient)
        effective_weight = base_weight * trust_score

        logger.debug(
            "[trust_runtime] weighted observation: %s → %s (base=%.2f, trust=%.2f, weight=%.2f)",
            origin,
            recipient,
            base_weight,
            trust_score,
            effective_weight,
        )
        _obs.gauge("trust.weight", effective_weight, origin=origin, recipient=recipient)

        return effective_weight

    # ── introspection ──────────────────────────────────────────────────────────

    def score_origin(self, origin: str, recipient: str | None = None) -> float:
        """Get trust score for an origin."""
        return self.trust(origin, recipient)

    def outcome_history(self) -> list[dict]:
        """Access learning history for auditing."""
        return list(self._outcome_history)

    def recent_outcomes(self, origin: str, recipient: str | None = None, limit: int = 10) -> list[dict]:
        """Recent learning outcomes for this origin→recipient pair."""
        if recipient is None:
            recipient = self._actor_id

        return [e for e in self._outcome_history if e["origin"] == origin and e["recipient"] == recipient][-limit:]

    def summary(self) -> dict[str, Any]:
        """Trust runtime summary for introspection."""
        return {
            "type": "TrustRuntime",
            "actor": self._actor_id,
            "graph": type(self._network).__name__ if self._network else "None",
            "queries_tracked": len(self._last_query),
            "outcomes_learned": len(self._outcome_history),
            "cache_dirty": self._cache_invalidated,
        }
