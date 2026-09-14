"""Retrieval Policy — optimization-based knowledge retrieval.

Retrieval should NOT be: "always retrieve."
Retrieval SHOULD be: "retrieve if Expected Information Gain > Cost."

    Retrieve if  ΔL_sim > RetrievalCost
    or better:  ExpectedInformationGain > Cost

The cost includes:
    - Latency (time to retrieve)
    - Token cost (LLM tokens consumed)
    - Compute cost (embedding, ranking)
    - Staleness risk (retrieving old knowledge may hurt)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.knowledge.item import KnowledgeItem
from src.knowledge.pack import KnowledgePack


@dataclass
class RetrievalCost:
    """Cost of retrieving and using knowledge."""

    latency_ms: float = 100.0  # time cost
    token_cost: float = 0.0  # LLM token cost
    compute_cost: float = 0.0  # embedding/ranking cost
    staleness_penalty: float = 0.0  # risk of stale knowledge

    @property
    def total(self) -> float:
        """Normalized total cost [0, 1]."""
        return min(
            1.0,
            (
                0.3 * min(self.latency_ms / 1000, 1.0)
                + 0.3 * min(self.token_cost / 1000, 1.0)
                + 0.2 * min(self.compute_cost / 100, 1.0)
                + 0.2 * self.staleness_penalty
            ),
        )


@dataclass
class RetrievalDecision:
    """Outcome of a retrieval decision."""

    should_retrieve: bool = False
    expected_information_gain: float = 0.0
    retrieval_cost: float = 0.0
    net_benefit: float = 0.0  # gain - cost
    confidence_improvement: float = 0.0
    items_to_retrieve: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_retrieve": self.should_retrieve,
            "expected_information_gain": self.expected_information_gain,
            "retrieval_cost": self.retrieval_cost,
            "net_benefit": self.net_benefit,
            "confidence_improvement": self.confidence_improvement,
            "items_count": len(self.items_to_retrieve),
            "reason": self.reason,
        }


class HeuristicRetrievalPolicy:
    """Decides when to retrieve knowledge.

    Session 3: The retrieval optimization.

    Core principle:
        Retrieve if  E[ΔL_sim] > Cost
        i.e., the expected reduction in simulation loss exceeds the retrieval cost.
    """

    def __init__(
        self,
        min_net_benefit: float = 0.05,
        min_information_gain: float = 0.1,
        max_items_per_retrieval: int = 10,
        cost_weight_latency: float = 0.3,
        cost_weight_tokens: float = 0.3,
        cost_weight_compute: float = 0.2,
        cost_weight_staleness: float = 0.2,
    ):
        self.min_net_benefit = min_net_benefit
        self.min_information_gain = min_information_gain
        self.max_items_per_retrieval = max_items_per_retrieval
        self._cost_weights = {
            "latency": cost_weight_latency,
            "tokens": cost_weight_tokens,
            "compute": cost_weight_compute,
            "staleness": cost_weight_staleness,
        }
        self._decisions: list[RetrievalDecision] = []
        self._max_decisions = 1000

    def evaluate(
        self,
        current_knowledge: KnowledgePack,
        candidates: list[KnowledgeItem],
        current_simulation_loss: float,
        retrieval_cost: RetrievalCost | None = None,
    ) -> RetrievalDecision:
        """Evaluate whether to retrieve knowledge.

        Args:
            current_knowledge: existing knowledge pack
            candidates: candidate items to retrieve
            current_simulation_loss: L_sim before retrieval
            retrieval_cost: cost of retrieval (estimated)

        Returns:
            RetrievalDecision with should_retrieve, items, and reasoning.
        """
        if not candidates:
            return RetrievalDecision(reason="no candidates")

        # Estimate current knowledge confidence
        current_fusion = current_knowledge.fuse()
        current_confidence = current_fusion.effective_knowledge

        # ASSUMPTION: information gain is independent across candidate items.
        # No correlation penalty when multiple items cover the same knowledge.
        scored_candidates = []
        for item in candidates:
            # Expected confidence improvement
            item_conf = item.composite_confidence
            # How much would this item improve the pack?
            # Improvement = (item_conf - current_confidence) × relevance
            relevance = item.information_gain_potential
            improvement = max(0, (item_conf - current_confidence * 0.5)) * relevance
            scored_candidates.append((item, improvement))

        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        top_candidates = scored_candidates[: self.max_items_per_retrieval]

        # Expected information gain from top candidates
        total_improvement = sum(imp for _, imp in top_candidates)
        # Scale by how much the pack would improve
        pack_improvement = min(1.0 - current_confidence, total_improvement * 0.3)
        expected_info_gain = pack_improvement * current_simulation_loss

        # Estimate retrieval cost
        cost = retrieval_cost or RetrievalCost(
            latency_ms=50 * len(top_candidates),
            token_cost=100 * len(top_candidates),
            compute_cost=10 * len(top_candidates),
        )
        cost_score = cost.total

        # Net benefit
        net_benefit = expected_info_gain - cost_score

        # Decision
        should_retrieve = net_benefit >= self.min_net_benefit and expected_info_gain >= self.min_information_gain

        # Confidence improvement estimate
        conf_improvement = pack_improvement if should_retrieve else 0.0

        # Reason
        if not should_retrieve:
            if expected_info_gain < self.min_information_gain:
                reason = f"information gain {expected_info_gain:.3f} < min {self.min_information_gain}"
            else:
                reason = f"net benefit {net_benefit:.3f} < min {self.min_net_benefit}"
        else:
            reason = (
                f"gain={expected_info_gain:.3f} cost={cost_score:.3f} net={net_benefit:.3f} items={len(top_candidates)}"
            )

        decision = RetrievalDecision(
            should_retrieve=should_retrieve,
            expected_information_gain=expected_info_gain,
            retrieval_cost=cost_score,
            net_benefit=net_benefit,
            confidence_improvement=conf_improvement,
            items_to_retrieve=[item.id for item, _ in top_candidates],
            reason=reason,
        )

        self._decisions.append(decision)
        if len(self._decisions) > self._max_decisions:
            self._decisions = self._decisions[-self._max_decisions :]
        return decision

    def update(self, cost: float, gained: int) -> None:
        """Adapt retrieval thresholds based on observed cost and knowledge gain.

        Called by CognitiveKernel._update_learning() after each EPA step.
        Alpha is learned from retrieval outcome history:
          - High gain with low cost → lower alpha (be less aggressive)
          - Low gain with high cost → higher alpha (be more conservative)
        """
        # Adaptive alpha: higher when cost is high relative to gain
        ratio = cost / max(gained, 1)
        alpha = min(0.3, max(0.02, ratio * 0.1))
        self.min_net_benefit = round((1 - alpha) * self.min_net_benefit + alpha * cost, 4)

    def get_stats(self) -> dict[str, Any]:
        """Get statistics over past decisions."""
        if not self._decisions:
            return {"total": 0, "retrieved": 0, "retrieval_rate": 0.0}

        total = len(self._decisions)
        retrieved = sum(1 for d in self._decisions if d.should_retrieve)
        avg_gain = sum(d.expected_information_gain for d in self._decisions) / total
        avg_cost = sum(d.retrieval_cost for d in self._decisions) / total

        return {
            "total": total,
            "retrieved": retrieved,
            "skipped": total - retrieved,
            "retrieval_rate": retrieved / total,
            "avg_information_gain": avg_gain,
            "avg_retrieval_cost": avg_cost,
            "avg_net_benefit": sum(d.net_benefit for d in self._decisions) / total,
        }
