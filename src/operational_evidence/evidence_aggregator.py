"""Evidence Aggregator — Aggregates evidence across multiple executions to detect patterns."""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Tuple
from .evidence_package import EvidencePackage


class EvidenceAggregator:
    """Aggregates evidence across multiple executions to detect recurring patterns."""

    def __init__(self):
        self.evidence_packages: List[EvidencePackage] = []
        self.observation_counter: Counter = Counter()
        self.recommendation_counter: Counter = Counter()
        self.error_counter: Counter = Counter()
        self.fallback_counter: Counter = Counter()

    def add_evidence(self, evidence: EvidencePackage) -> None:
        """Add an evidence package to the aggregator."""
        self.evidence_packages.append(evidence)

        # Update counters
        for observation in evidence.observations:
            self.observation_counter[observation] += 1

        for recommendation in evidence.recommendations:
            self.recommendation_counter[recommendation] += 1

        for error in evidence.error_details:
            error_key = f"{error['type']}: {error['message']}"
            self.error_counter[error_key] += 1

        for fallback_type, count in evidence.runtime.fallbacks.items():
            self.fallback_counter[fallback_type] += count

    def get_top_observations(self, top_n: int = 5) -> List[Tuple[str, int]]:
        """Get the most common observations across all executions."""
        return self.observation_counter.most_common(top_n)

    def get_top_recommendations(self, top_n: int = 5) -> List[Tuple[str, int]]:
        """Get the most common recommendations across all executions."""
        return self.recommendation_counter.most_common(top_n)

    def get_top_errors(self, top_n: int = 5) -> List[Tuple[str, int]]:
        """Get the most common errors across all executions."""
        return self.error_counter.most_common(top_n)

    def get_top_fallbacks(self, top_n: int = 5) -> List[Tuple[str, int]]:
        """Get the most common fallback types across all executions."""
        return self.fallback_counter.most_common(top_n)

    def calculate_engineering_kpis(self) -> Dict[str, float]:
        """Calculate engineering KPIs from aggregated evidence."""
        if not self.evidence_packages:
            return {
                "self_healing_success_rate": 0.0,
                "average_fix_time_ms": 0.0,
                "autonomous_fixes": 0,
                "regression_rate": 0.0,
                "recurring_architecture_issues": 0,
                "human_intervention_rate": 0.0,
            }

        total_executions = len(self.evidence_packages)
        successful_executions = sum(1 for ep in self.evidence_packages if ep.status == "SUCCESS")
        total_duration = sum(ep.runtime.duration_ms for ep in self.evidence_packages)

        # Calculate KPIs
        self_healing_success_rate = (successful_executions / total_executions) * 100 if total_executions > 0 else 0.0
        average_fix_time = total_duration / total_executions if total_executions > 0 else 0.0
        autonomous_fixes = sum(ep.code.files_modified for ep in self.evidence_packages)

        # Regression rate (executions that failed after previous success)
        regressions = 0
        for i in range(1, len(self.evidence_packages)):
            if self.evidence_packages[i - 1].status == "SUCCESS" and self.evidence_packages[i].status != "SUCCESS":
                regressions += 1

        regression_rate = (regressions / max(1, total_executions - 1)) * 100 if total_executions > 1 else 0.0

        # Recurring architecture issues (observations seen multiple times)
        recurring_issues = sum(1 for count in self.observation_counter.values() if count > 1)

        # Human intervention rate (manual resets, etc.)
        human_interventions = sum(
            1 for ep in self.evidence_packages if "manual" in ep.context.get("intervention_type", "").lower()
        )
        human_intervention_rate = (human_interventions / total_executions) * 100 if total_executions > 0 else 0.0

        return {
            "self_healing_success_rate": round(self_healing_success_rate, 1),
            "average_fix_time_ms": round(average_fix_time, 1),
            "autonomous_fixes": autonomous_fixes,
            "regression_rate": round(regression_rate, 1),
            "recurring_architecture_issues": recurring_issues,
            "technical_debt_trend": -recurring_issues * 5,  # Simple proxy for technical debt reduction
            "specification_improvements": len(self.recommendation_counter),
            "human_intervention_rate": round(human_intervention_rate, 1),
        }

    def get_evidence_trends(self) -> Dict[str, List[float]]:
        """Get trends over time for key metrics."""
        trends: Dict[str, List[float]] = {
            "success_rate": [],
            "fix_time": [],
            "files_modified": [],
            "confidence": [],
        }

        for i, evidence in enumerate(self.evidence_packages):
            trends["success_rate"].append(1.0 if evidence.status == "SUCCESS" else 0.0)
            trends["fix_time"].append(evidence.runtime.duration_ms)
            trends["files_modified"].append(evidence.code.files_modified)
            trends["confidence"].append(evidence.confidence)

        return trends

    def generate_architecture_review(self) -> Dict[str, Any]:
        """Generate an architecture review based on aggregated evidence."""
        top_observations = self.get_top_observations(3)
        top_recommendations = self.get_top_recommendations(3)
        top_errors = self.get_top_errors(3)

        return {
            "review_id": f"arch-review-{len(self.evidence_packages)}",
            "timestamp": (self.evidence_packages[-1].timestamp if self.evidence_packages else ""),
            "total_executions_analyzed": len(self.evidence_packages),
            "top_findings": [
                {
                    "issue": observation,
                    "occurrences": count,
                    "severity": ("high" if count > 5 else "medium" if count > 2 else "low"),
                }
                for observation, count in top_observations
            ],
            "top_recommendations": [
                {
                    "recommendation": recommendation,
                    "priority": "high" if count > 3 else "medium",
                }
                for recommendation, count in top_recommendations
            ],
            "recurring_errors": [{"error": error, "count": count} for error, count in top_errors],
            "architecture_health_score": self._calculate_health_score(),
            "specification_update_recommended": len(top_recommendations) > 0,
        }

    def _calculate_health_score(self) -> float:
        """Calculate an overall architecture health score (0-100)."""
        if not self.evidence_packages:
            return 50.0  # Neutral score if no data

        # Base score from success rate
        success_rate = sum(1 for ep in self.evidence_packages if ep.status == "SUCCESS") / len(self.evidence_packages)
        score = success_rate * 100

        # Adjust for recurring issues
        recurring_issues = sum(1 for count in self.observation_counter.values() if count > 1)
        score -= recurring_issues * 2  # Penalty for each recurring issue

        # Bonus for successful autonomous fixes
        total_fixes = sum(ep.code.files_modified for ep in self.evidence_packages)
        if total_fixes > 0:
            score += min(total_fixes * 0.5, 10)  # Cap bonus at 10 points

        # Ensure score is between 0 and 100
        return max(0, min(100, round(score, 1)))

    def get_evidence_count(self) -> int:
        """Get the number of evidence packages collected."""
        return len(self.evidence_packages)

    def clear_evidence(self) -> None:
        """Clear all collected evidence."""
        self.evidence_packages.clear()
        self.observation_counter.clear()
        self.recommendation_counter.clear()
        self.error_counter.clear()
        self.fallback_counter.clear()
