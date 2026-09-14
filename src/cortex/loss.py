"""Loss Engine — computes loss across multiple dimensions.

Loss functions are independently configurable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LossResult:
    """Result of loss computation."""

    total_loss: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class LossEngine:
    """Computes loss across multiple dimensions.

    Loss dimensions:
    - Intent Loss
    - Execution Loss
    - Answer Quality Loss
    - Grounding Loss
    - Latency Loss
    - Capability Loss
    """

    def __init__(self):
        self._weights: dict[str, float] = {
            "intent_loss": 0.2,
            "execution_loss": 0.2,
            "answer_quality_loss": 0.3,
            "grounding_loss": 0.2,
            "latency_loss": 0.1,
            "capability_loss": 0.1,
        }

    def compute(
        self,
        success: bool,
        latency_ms: float = 0.0,
        ground_truth: dict[str, Any] | None = None,
        answer: str = "",
        intent: str = "",
        expected_intent: str = "",
        capabilities: list[str] | None = None,
        expected_capabilities: list[str] | None = None,
    ) -> LossResult:
        """Compute loss from execution outcomes."""
        components = {}

        # Intent loss
        if expected_intent:
            components["intent_loss"] = 0.0 if intent == expected_intent else 1.0
        else:
            components["intent_loss"] = 0.0

        # Execution loss
        components["execution_loss"] = 0.0 if success else 1.0

        # Answer quality loss
        if ground_truth and answer:
            gt_answer = ground_truth.get("answer", "")
            if gt_answer:
                # Simple string similarity
                common = set(gt_answer.lower().split()) & set(answer.lower().split())
                total = set(gt_answer.lower().split()) | set(answer.lower().split())
                similarity = len(common) / len(total) if total else 0.0
                components["answer_quality_loss"] = 1.0 - similarity
            else:
                components["answer_quality_loss"] = 0.0
        else:
            components["answer_quality_loss"] = 0.0

        # Grounding loss
        if ground_truth and answer:
            gt_keywords = set(ground_truth.get("keywords", []))
            answer_words = set(answer.lower().split())
            if gt_keywords:
                overlap = len(gt_keywords & answer_words)
                components["grounding_loss"] = 1.0 - (overlap / len(gt_keywords))
            else:
                components["grounding_loss"] = 0.0
        else:
            components["grounding_loss"] = 0.0

        # Latency loss (target: <100ms)
        target_latency = 100.0
        components["latency_loss"] = min(1.0, max(0.0, (latency_ms - target_latency) / target_latency))

        # Capability loss
        if expected_capabilities and capabilities:
            expected_set = set(expected_capabilities)
            actual_set = set(capabilities)
            if expected_set:
                components["capability_loss"] = 1.0 - len(expected_set & actual_set) / len(expected_set)
            else:
                components["capability_loss"] = 0.0
        else:
            components["capability_loss"] = 0.0

        # Compute weighted total
        total = 0.0
        for component, value in components.items():
            weight = self._weights.get(component, 0.0)
            total += value * weight

        return LossResult(
            total_loss=total,
            components=components,
        )
