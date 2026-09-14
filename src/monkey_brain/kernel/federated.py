"""Federated Learning — cross-runtime Q-value aggregation.

Runtimes exchange Q-table summaries (not raw transitions) to improve
learning without sharing private execution data.  Uses simple averaging
with outlier rejection (Byzantine fault tolerance lite).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.federated")


@dataclass
class QTableSummary:
    """A summary of a runtime's Q-table for federated exchange."""

    runtime_id: str
    q_values: dict[str, float] = field(default_factory=dict)
    timestamp: float = 0.0
    signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "q_values": self.q_values,
            "timestamp": self.timestamp,
            "signature": self.signature,
        }


@dataclass
class FederatedResult:
    """Result of federated aggregation."""

    merged_count: int = 0
    outlier_count: int = 0
    participants: list[str] = field(default_factory=list)
    avg_q_before: float = 0.0
    avg_q_after: float = 0.0


class FederatedLearner:
    """Aggregates Q-values across runtimes using federated averaging.

    Each runtime exports a QTableSummary (its Q-values).  The learner
    aggregates them using trimmed mean (rejects outliers) and produces
    an updated Q-table.
    """

    def __init__(self, trim_ratio: float = 0.1) -> None:
        self._trim_ratio = trim_ratio  # fraction of outliers to reject
        self._summaries: dict[str, QTableSummary] = {}

    def add_summary(self, summary: QTableSummary) -> None:
        """Add a Q-table summary from a runtime."""
        self._summaries[summary.runtime_id] = summary

    def aggregate(self) -> dict[str, float]:
        """Aggregate all summaries using trimmed mean.

        For each agent, collects Q-values from all runtimes,
        trims the top/bottom outliers, and averages the rest.
        """
        if not self._summaries:
            return {}

        # Collect all Q-values per agent
        agent_values: dict[str, list[tuple[str, float]]] = {}
        for rid, summary in self._summaries.items():
            for agent, q in summary.q_values.items():
                agent_values.setdefault(agent, []).append((rid, q))

        # Trimmed mean per agent
        aggregated = {}
        for agent, values in agent_values.items():
            if len(values) <= 2:
                # Too few to trim — simple average
                aggregated[agent] = sum(q for _, q in values) / len(values)
                continue

            # Sort by value and trim extremes
            sorted_vals = sorted(values, key=lambda x: x[1])
            trim_count = max(1, int(len(sorted_vals) * self._trim_ratio))
            trimmed = sorted_vals[trim_count:-trim_count] if trim_count < len(sorted_vals) // 2 else sorted_vals

            aggregated[agent] = sum(q for _, q in trimmed) / max(len(trimmed), 1)

        return aggregated

    def detect_outliers(self) -> list[str]:
        """Identify runtimes whose Q-values deviate significantly from the group."""
        if len(self._summaries) < 3:
            return []

        # Compute mean Q-value per runtime
        runtime_means = {}
        for rid, summary in self._summaries.items():
            if summary.q_values:
                runtime_means[rid] = sum(summary.q_values.values()) / len(summary.q_values)

        if not runtime_means:
            return []

        overall_mean = sum(runtime_means.values()) / len(runtime_means)
        overall_std = (sum((m - overall_mean) ** 2 for m in runtime_means.values()) / len(runtime_means)) ** 0.5

        outliers = []
        for rid, mean in runtime_means.items():
            if overall_std > 0 and abs(mean - overall_mean) > 2 * overall_std:
                outliers.append(rid)

        return outliers

    def summary_count(self) -> int:
        return len(self._summaries)
