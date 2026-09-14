"""Conflict Resolution Layer — resolves conflicting beliefs between actors.

When actors gossip, they may have different beliefs about the same state.
This module provides strategies for detecting and resolving these conflicts.

Conflict Resolution Strategies:
  - majority_vote: Use the most common belief
  - highest_confidence: Use the belief with highest confidence
  - most_recent: Use the most recently observed belief
  - trust_weighted: Weight beliefs by trust score
  - keep_local: Keep the local actor's belief (ignore gossip)
  - merge: Combine beliefs (average, weighted average)

Architectural Invariant:
    Conflict resolution operates on beliefs, not on the world tensor.
    The world tensor remains the source of truth.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor

logger = logging.getLogger("agentos.conflict_resolution")


class ResolutionStrategy(str, Enum):
    """Strategies for resolving conflicting beliefs."""

    MAJORITY_VOTE = "majority_vote"
    HIGHEST_CONFIDENCE = "highest_confidence"
    MOST_RECENT = "most_recent"
    TRUST_WEIGHTED = "trust_weighted"
    KEEP_LOCAL = "keep_local"
    MERGE = "merge"


@dataclass
class BeliefConflict:
    """A conflict between two actors' beliefs about the same transition."""

    src: str
    dst: str
    domain: str
    actor_a: str
    actor_b: str
    value_a: float
    value_b: float
    confidence_a: float
    confidence_b: float
    timestamp_a: float
    timestamp_b: float
    trust_score: float = 0.5  # trust between actors

    @property
    def severity(self) -> float:
        """Conflict severity: 0 = no conflict, 1 = maximum disagreement."""
        if self.value_a == self.value_b:
            return 0.0
        return abs(self.value_a - self.value_b) / max(abs(self.value_a), abs(self.value_b), 1.0)


class ConflictResolver:
    """Resolves conflicting beliefs between actors.

    When actors gossip, they may have different beliefs about the same state.
    This resolver detects conflicts and applies a resolution strategy.
    """

    def __init__(self, strategy: ResolutionStrategy = ResolutionStrategy.TRUST_WEIGHTED):
        self._strategy = strategy
        self._conflicts: list[BeliefConflict] = []
        self._resolution_count: int = 0

    def detect_conflicts(
        self,
        belief_a: SparseTransitionTensor,
        belief_b: SparseTransitionTensor,
        actor_a: str,
        actor_b: str,
        trust_score: float = 0.5,
    ) -> list[BeliefConflict]:
        """Detect conflicts between two actors' beliefs.

        A conflict exists when both actors have observed the same transition
        but with materially different values.
        """
        conflicts = []
        seen = set()

        for src_a, dst_a in belief_a:
            key = (src_a, dst_a)
            if key in seen:
                continue

            if belief_b.has_edge(src_a, dst_a):
                val_a = belief_a.feature(src_a, dst_a, Feature.PROBABILITY)
                val_b = belief_b.feature(src_a, dst_a, Feature.PROBABILITY)

                if abs(val_a - val_b) > 0.01:  # Material difference
                    conf_a = belief_a.feature(src_a, dst_a, Feature.CONFIDENCE)
                    conf_b = belief_b.feature(src_a, dst_a, Feature.CONFIDENCE)
                    ts_a = belief_a.feature(src_a, dst_a, Feature.RECENCY)
                    ts_b = belief_b.feature(src_a, dst_a, Feature.RECENCY)

                    conflict = BeliefConflict(
                        src=src_a,
                        dst=dst_a,
                        domain=belief_a.domain_of(src_a),
                        actor_a=actor_a,
                        actor_b=actor_b,
                        value_a=val_a,
                        value_b=val_b,
                        confidence_a=conf_a,
                        confidence_b=conf_b,
                        timestamp_a=ts_a,
                        timestamp_b=ts_b,
                        trust_score=trust_score,
                    )
                    conflicts.append(conflict)
                    seen.add(key)

        return conflicts

    def resolve(
        self,
        conflict: BeliefConflict,
        trust_network: Any = None,
    ) -> tuple[float, str]:
        """Resolve a conflict using the configured strategy.

        Returns (resolved_value, strategy_used).
        """
        self._conflicts.append(conflict)
        self._resolution_count += 1

        if self._strategy == ResolutionStrategy.MAJORITY_VOTE:
            return self._resolve_majority_vote(conflict)
        elif self._strategy == ResolutionStrategy.HIGHEST_CONFIDENCE:
            return self._resolve_highest_confidence(conflict)
        elif self._strategy == ResolutionStrategy.MOST_RECENT:
            return self._resolve_most_recent(conflict)
        elif self._strategy == ResolutionStrategy.TRUST_WEIGHTED:
            return self._resolve_trust_weighted(conflict)
        elif self._strategy == ResolutionStrategy.KEEP_LOCAL:
            return self._resolve_keep_local(conflict)
        elif self._strategy == ResolutionStrategy.MERGE:
            return self._resolve_merge(conflict)
        else:
            return self._resolve_keep_local(conflict)

    def _resolve_majority_vote(self, conflict: BeliefConflict) -> tuple[float, str]:
        """Use the belief with higher confidence (proxy for majority)."""
        if conflict.confidence_a >= conflict.confidence_b:
            return conflict.value_a, "majority_vote"
        return conflict.value_b, "majority_vote"

    def _resolve_highest_confidence(self, conflict: BeliefConflict) -> tuple[float, str]:
        """Use the belief with highest confidence."""
        if conflict.confidence_a >= conflict.confidence_b:
            return conflict.value_a, "highest_confidence"
        return conflict.value_b, "highest_confidence"

    def _resolve_most_recent(self, conflict: BeliefConflict) -> tuple[float, str]:
        """Use the most recently observed belief."""
        if conflict.timestamp_a >= conflict.timestamp_b:
            return conflict.value_a, "most_recent"
        return conflict.value_b, "most_recent"

    def _resolve_trust_weighted(self, conflict: BeliefConflict) -> tuple[float, str]:
        """Weight beliefs by trust score."""
        # Higher trust = more weight
        weight_a = conflict.trust_score
        weight_b = 1.0 - conflict.trust_score
        total = weight_a + weight_b
        if total == 0:
            return (conflict.value_a + conflict.value_b) / 2, "trust_weighted"
        resolved = (conflict.value_a * weight_a + conflict.value_b * weight_b) / total
        return resolved, "trust_weighted"

    def _resolve_keep_local(self, conflict: BeliefConflict) -> tuple[float, str]:
        """Keep the local actor's belief (actor_a)."""
        return conflict.value_a, "keep_local"

    def _resolve_merge(self, conflict: BeliefConflict) -> tuple[float, str]:
        """Average the two beliefs."""
        resolved = (conflict.value_a + conflict.value_b) / 2
        return resolved, "merge"

    def get_conflicts(self) -> list[BeliefConflict]:
        """Return all detected conflicts."""
        return self._conflicts.copy()

    def summary(self) -> dict:
        return {
            "strategy": self._strategy.value,
            "total_conflicts": len(self._conflicts),
            "resolution_count": self._resolution_count,
        }
