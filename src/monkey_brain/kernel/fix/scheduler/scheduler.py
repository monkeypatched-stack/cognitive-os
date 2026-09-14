"""Reasoning Scheduler — selects reasoning topology.

Strategies:
    Chain of Thought (CoT) — sequential reasoning
    Graph of Thoughts (GoT) — parallel independent reasoning
    Tree of Thoughts (ToT) — branching exploration
    Parallel Debate — adversarial multi-agent
    Adversarial Review — falsification-first
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class ReasoningStrategy(StrEnum):
    CHAIN_OF_THOUGHT = "cot"
    GRAPH_OF_THOUGHTS = "got"
    TREE_OF_THOUGHTS = "tot"
    PARALLEL_DEBATE = "debate"
    ADVERSARIAL_REVIEW = "adversarial"
    PROPOSAL_CRITIQUE_REPAIR = "pcr"


@dataclass
class ReasoningPlan:
    """Selected reasoning topology for a problem."""

    strategy: ReasoningStrategy = ReasoningStrategy.CHAIN_OF_THOUGHT
    steps: list[dict[str, Any]] = field(default_factory=list)
    parallelism: int = 1  # how many parallel branches
    max_depth: int = 5  # for ToT
    agents_required: int = 1  # for debate/adversarial
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "parallelism": self.parallelism,
            "max_depth": self.max_depth,
            "agents_required": self.agents_required,
            "confidence": self.confidence,
        }


class HeuristicReasoningScheduler:
    """Selects reasoning topology based on problem structure."""

    def select(
        self,
        problem: dict[str, Any],
        available_agents: int = 1,
        confidence_threshold: float = 0.7,
    ) -> ReasoningPlan:
        """Select the best reasoning strategy.

        Heuristics:
            - Simple/sequential → CoT
            - Multiple independent aspects → GoT
            - Needs exploration → ToT
            - Controversial/uncertain → Debate
            - High-stakes verification → Adversarial
        """
        problem_type = problem.get("type", "general")
        complexity = problem.get("complexity", "medium")
        uncertainty = problem.get("uncertainty", 0.5)
        needs_verification = problem.get("needs_verification", False)

        # Selection logic
        if needs_verification:
            return ReasoningPlan(
                strategy=ReasoningStrategy.ADVERSARIAL_REVIEW,
                parallelism=3,
                agents_required=3,
                confidence=0.8,
            )

        if uncertainty > 0.7 and available_agents >= 3:
            return ReasoningPlan(
                strategy=ReasoningStrategy.PARALLEL_DEBATE,
                parallelism=available_agents,
                agents_required=min(available_agents, 5),
                confidence=0.75,
            )

        if complexity == "high" and problem_type in (
            "planning",
            "design",
            "architecture",
        ):
            return ReasoningPlan(
                strategy=ReasoningStrategy.TREE_OF_THOUGHTS,
                parallelism=3,
                max_depth=5,
                agents_required=3,
                confidence=0.7,
            )

        if problem_type in ("analysis", "comparison", "evaluation") and available_agents >= 2:
            return ReasoningPlan(
                strategy=ReasoningStrategy.GRAPH_OF_THOUGHTS,
                parallelism=min(available_agents, 4),
                agents_required=min(available_agents, 4),
                confidence=0.75,
            )

        if problem_type in ("critique", "review", "repair") and available_agents >= 2:
            return ReasoningPlan(
                strategy=ReasoningStrategy.PROPOSAL_CRITIQUE_REPAIR,
                parallelism=2,
                agents_required=2,
                confidence=0.7,
            )

        # Default: Chain of Thought
        return ReasoningPlan(
            strategy=ReasoningStrategy.CHAIN_OF_THOUGHT,
            parallelism=1,
            agents_required=1,
            confidence=0.6,
        )
