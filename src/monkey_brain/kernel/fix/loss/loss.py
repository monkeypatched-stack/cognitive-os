"""Loss — quantifies prediction error.

Loss measures how far the actual outcome deviates from the expected outcome.
Loss = 0.0 means perfect prediction.
Loss = 1.0 means maximum error.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Loss:
    """Quantifies prediction error across multiple dimensions."""

    value: float = 1.0  # 0.0 = perfect, 1.0 = maximum error
    components: dict[str, float] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def is_perfect(self) -> bool:
        return self.value <= 0.0

    def is_acceptable(self, threshold: float = 0.3) -> bool:
        return self.value <= threshold


def compute_loss(predicted: dict, actual: dict) -> Loss:
    """Compute loss between predicted and actual outcomes.

    Compares:
    - Entity matching (names, statuses)
    - Count accuracy
    - Answer completeness
    """
    components = {}

    # Entity name matching
    pred_names = set(predicted.get("names", []))
    actual_names = set(actual.get("names", []))
    if actual_names:
        name_matches = len(pred_names & actual_names)
        components["name_accuracy"] = name_matches / len(actual_names)
    else:
        components["name_accuracy"] = 1.0 if not pred_names else 0.5

    # Count matching
    pred_count = predicted.get("count", 0)
    actual_count = actual.get("count", 0)
    if actual_count > 0:
        components["count_accuracy"] = min(pred_count, actual_count) / actual_count
    else:
        components["count_accuracy"] = 1.0 if pred_count == 0 else 0.5

    # Status matching
    pred_statuses = set(predicted.get("statuses", []))
    actual_statuses = set(actual.get("statuses", []))
    if actual_statuses:
        status_matches = len(pred_statuses & actual_statuses)
        components["status_accuracy"] = status_matches / len(actual_statuses)
    else:
        components["status_accuracy"] = 1.0 if not pred_statuses else 0.5

    # Answer presence
    components["has_answer"] = 1.0 if actual.get("answer") else 0.0

    # Weighted average
    weights = {
        "name_accuracy": 0.3,
        "count_accuracy": 0.3,
        "status_accuracy": 0.2,
        "has_answer": 0.2,
    }

    total = sum(components[k] * weights.get(k, 0) for k in components)
    loss = 1.0 - total

    return Loss(
        value=max(0.0, min(1.0, loss)),
        components=components,
    )
