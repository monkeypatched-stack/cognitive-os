"""Epistemic Completeness — checks knowledge completeness."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CompletenessReport:
    """Report on epistemic completeness."""

    overall_confidence: float = 0.0
    coverage: float = 0.0
    gaps: list[str] = None

    def __post_init__(self):
        if self.gaps is None:
            self.gaps = []


class EpistemicCompleteness:
    """Assesses completeness of epistemic state."""

    def assess(
        self,
        coverage: float,
        consistency: float,
        gaps: float,
        depth: float,
        breadth: float,
        recency: float,
        reliability: float,
    ) -> CompletenessReport:
        """Assess epistemic completeness from multiple dimensions."""
        overall = (
            0.2 * coverage
            + 0.15 * consistency
            + 0.1 * max(0, 1 - gaps)
            + 0.15 * depth
            + 0.1 * breadth
            + 0.15 * recency
            + 0.15 * reliability
        )
        return CompletenessReport(
            overall_confidence=max(0.0, min(1.0, overall)),
            coverage=coverage,
        )
