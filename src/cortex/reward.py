"""Reward Engine — computes rewards from execution outcomes.

Reward functions are configurable.
Reward signals are normalized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RewardResult:
    """Result of reward computation."""

    total_reward: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    normalized: float = 0.0  # 0.0 to 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


class ExecutionRewardEngine:
    """Computes rewards from execution outcomes.

    Reward signals:
    - Correctness
    - User Feedback
    - Ground Truth Match
    - Execution Success
    - Latency
    - Resource Consumption
    - Capability Success
    - Workflow Success
    """

    def __init__(self):
        self._weights: dict[str, float] = {
            "correctness": 0.3,
            "user_feedback": 0.25,
            "execution_success": 0.2,
            "latency": 0.1,
            "capability_success": 0.1,
            "ground_truth_match": 0.05,
        }

    def compute(
        self,
        success: bool,
        latency_ms: float = 0.0,
        user_feedback: dict[str, Any] | None = None,
        ground_truth: dict[str, Any] | None = None,
        answer: str = "",
        capabilities: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> RewardResult:
        """Compute reward from execution outcomes."""
        components = {}

        # Execution success
        components["execution_success"] = 1.0 if success else 0.0

        # Latency reward (lower is better, max 100ms)
        if latency_ms > 0:
            components["latency"] = max(0.0, 1.0 - (latency_ms / 1000.0))
        else:
            components["latency"] = 1.0

        # User feedback
        if user_feedback:
            feedback_reward = 0.0
            if user_feedback.get("accepted"):
                feedback_reward += 1.0
            if user_feedback.get("thumb") == "up":
                feedback_reward += 0.5
            if user_feedback.get("thumb") == "down":
                feedback_reward -= 0.5
            if user_feedback.get("rating"):
                feedback_reward += (user_feedback["rating"] - 3) / 2
            components["user_feedback"] = max(-1.0, min(1.0, feedback_reward))
        else:
            components["user_feedback"] = 0.5  # Neutral

        # Ground truth match
        if ground_truth and answer:
            gt_answer = ground_truth.get("answer", "")
            if gt_answer and gt_answer.lower() in answer.lower():
                components["ground_truth_match"] = 1.0
            else:
                components["ground_truth_match"] = 0.0
        else:
            components["ground_truth_match"] = 0.5  # Neutral

        # Capability success
        if capabilities:
            components["capability_success"] = 1.0  # All succeeded if we got here
        else:
            components["capability_success"] = 0.0

        # Compute weighted total
        total = 0.0
        for component, value in components.items():
            weight = self._weights.get(component, 0.0)
            total += value * weight

        # Normalize to -1.0 to 1.0
        normalized = max(-1.0, min(1.0, total))

        return RewardResult(
            total_reward=total,
            components=components,
            normalized=normalized,
        )
