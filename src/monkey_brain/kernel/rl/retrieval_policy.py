"""RL Retrieval Policy — reinforcement learning-based knowledge retrieval.

This policy learns when to retrieve knowledge based on past outcomes.
It's different from the fixed RetrievalPolicy in fix/policy/retrieval.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RetrievalDecision:
    """Outcome of a retrieval decision."""

    should_retrieve: bool = False
    expected_information_gain: float = 0.0
    retrieval_cost: float = 0.0
    net_benefit: float = 0.0
    confidence_improvement: float = 0.0
    items_to_retrieve: list[str] = field(default_factory=list)
    reason: str = ""


class RLRetrievalPolicy:
    """RL-based retrieval policy that learns from outcomes."""

    def __init__(
        self,
        min_net_benefit: float = 0.05,
        min_information_gain: float = 0.1,
        max_items_per_retrieval: int = 10,
    ):
        self.min_net_benefit = min_net_benefit
        self.min_information_gain = min_information_gain
        self.max_items_per_retrieval = max_items_per_retrieval
        self._retrieval_count = 0
        self._skip_count = 0
        self._total_gain = 0.0
        self._total_cost = 0.0

    def evaluate(
        self,
        current_knowledge: Any,
        candidates: list[Any],
        current_simulation_loss: float,
        retrieval_cost: float = 0.1,
    ) -> RetrievalDecision:
        """Evaluate whether to retrieve knowledge.

        Args:
            current_knowledge: existing knowledge
            candidates: candidate items to retrieve
            current_simulation_loss: current loss value
            retrieval_cost: estimated cost of retrieval

        Returns:
            RetrievalDecision with should_retrieve and reasoning.
        """
        if not candidates:
            return RetrievalDecision(reason="no candidates")

        # Estimate expected information gain
        # Simple heuristic: gain is proportional to current loss and number of candidates
        expected_info_gain = min(1.0, current_simulation_loss * len(candidates) * 0.1)

        # Net benefit calculation
        net_benefit = expected_info_gain - retrieval_cost

        # Decision
        should_retrieve = net_benefit >= self.min_net_benefit and expected_info_gain >= self.min_information_gain

        # Update statistics
        if should_retrieve:
            self._retrieval_count += 1
            self._total_gain += expected_info_gain
            self._total_cost += retrieval_cost
        else:
            self._skip_count += 1

        # Reason
        if not should_retrieve:
            if expected_info_gain < self.min_information_gain:
                reason = f"information gain {expected_info_gain:.3f} < min {self.min_information_gain}"
            else:
                reason = f"net benefit {net_benefit:.3f} < min {self.min_net_benefit}"
        else:
            reason = (
                f"gain={expected_info_gain:.3f} cost={retrieval_cost:.3f} net={net_benefit:.3f} items={len(candidates)}"
            )

        # Select top candidates (simple selection for RL policy)
        items_to_retrieve = [str(i) for i in candidates[: self.max_items_per_retrieval]]

        return RetrievalDecision(
            should_retrieve=should_retrieve,
            expected_information_gain=expected_info_gain,
            retrieval_cost=retrieval_cost,
            net_benefit=net_benefit,
            confidence_improvement=expected_info_gain * 0.5,
            items_to_retrieve=items_to_retrieve,
            reason=reason,
        )

    def update(self, cost: float, gained: int) -> None:
        """Update policy based on observed outcome.

        Args:
            cost: actual cost incurred
            gained: actual knowledge gained (1 if helpful, 0 otherwise)
        """
        # Simple adaptive learning: adjust thresholds based on outcomes
        if gained > 0:
            # If retrieval was helpful, be more aggressive
            self.min_net_benefit = max(0.01, self.min_net_benefit * 0.95)
        else:
            # If retrieval wasn't helpful, be more conservative
            self.min_net_benefit = min(0.2, self.min_net_benefit * 1.05)

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about retrieval decisions."""
        total = self._retrieval_count + self._skip_count
        return {
            "retrieval_count": self._retrieval_count,
            "skip_count": self._skip_count,
            "retrieval_rate": self._retrieval_count / total if total > 0 else 0.0,
            "avg_gain": self._total_gain / max(1, self._retrieval_count),
            "avg_cost": self._total_cost / max(1, self._retrieval_count),
            "min_net_benefit": self.min_net_benefit,
            "min_information_gain": self.min_information_gain,
        }
