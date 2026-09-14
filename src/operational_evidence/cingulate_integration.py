"""Cingulate Integration — Evidence-driven architecture review and specification evolution."""

from __future__ import annotations

from typing import Dict, List, Any
from .evidence_aggregator import EvidenceAggregator


class CingulateEvidenceReview:
    """Integrates operational evidence with Cingulate for architecture evolution."""

    def __init__(self, evidence_aggregator: EvidenceAggregator):
        """Initialize Cingulate evidence review with an evidence aggregator."""
        self.evidence_aggregator = evidence_aggregator
        self.review_history: List[Dict[str, Any]] = []

    def perform_evidence_driven_review(self) -> Dict[str, Any]:
        """Perform an evidence-driven architecture review."""
        architecture_review = self.evidence_aggregator.generate_architecture_review()

        # Add engineering KPIs to the review
        kpis = self.evidence_aggregator.calculate_engineering_kpis()
        architecture_review["engineering_kpis"] = kpis

        # Generate specification recommendations
        spec_recommendations = self._generate_specification_recommendations()
        architecture_review["specification_recommendations"] = spec_recommendations

        # Store review in history
        self.review_history.append(architecture_review)

        return architecture_review

    def _generate_specification_recommendations(self) -> List[Dict[str, Any]]:
        """Generate specification recommendations based on aggregated evidence."""
        recommendations = []

        # Analyze top observations for specification improvements
        top_observations = self.evidence_aggregator.get_top_observations(5)
        for observation, count in top_observations:
            if count > 2:  # Only consider recurring observations
                recommendations.append(
                    {
                        "type": "specification_improvement",
                        "issue": observation,
                        "occurrences": count,
                        "recommended_action": f"Update specification to address: {observation}",
                        "priority": "high" if count > 5 else "medium",
                    }
                )

        # Analyze top errors for error handling improvements
        top_errors = self.evidence_aggregator.get_top_errors(3)
        for error, count in top_errors:
            recommendations.append(
                {
                    "type": "error_handling_improvement",
                    "issue": error,
                    "occurrences": count,
                    "recommended_action": f"Add error handling specification for: {error}",
                    "priority": "high",
                }
            )

        # Analyze fallbacks for resilience improvements
        top_fallbacks = self.evidence_aggregator.get_top_fallbacks(3)
        for fallback, count in top_fallbacks:
            recommendations.append(
                {
                    "type": "resilience_improvement",
                    "issue": f"Frequent {fallback} fallbacks",
                    "occurrences": count,
                    "recommended_action": f"Improve primary provider or enhance {fallback} fallback mechanism",
                    "priority": "medium",
                }
            )

        return recommendations

    def get_architecture_health_trend(self) -> List[Dict[str, Any]]:
        """Get the architecture health trend over time."""
        return [
            {
                "review_id": review["review_id"],
                "timestamp": review["timestamp"],
                "health_score": review["architecture_health_score"],
                "executions_analyzed": review["total_executions_analyzed"],
            }
            for review in self.review_history
        ]

    def get_specification_evolution_metrics(self) -> Dict[str, Any]:
        """Get metrics on specification evolution over time."""
        if not self.review_history:
            return {
                "total_reviews": 0,
                "specification_updates": 0,
                "improvement_rate": 0.0,
                "current_health_score": 50.0,
            }

        total_reviews = len(self.review_history)
        specification_updates = sum(
            1 for review in self.review_history if review.get("specification_update_recommended", False)
        )

        # Calculate improvement rate
        if total_reviews > 1:
            initial_score = self.review_history[0]["architecture_health_score"]
            current_score = self.review_history[-1]["architecture_health_score"]
            improvement_rate = ((current_score - initial_score) / initial_score) * 100 if initial_score > 0 else 0.0
        else:
            improvement_rate = 0.0

        return {
            "total_reviews": total_reviews,
            "specification_updates": specification_updates,
            "improvement_rate": round(improvement_rate, 1),
            "current_health_score": self.review_history[-1]["architecture_health_score"],
            "specification_update_rate": (
                round((specification_updates / total_reviews) * 100, 1) if total_reviews > 0 else 0.0
            ),
        }

    def generate_soma_update_proposal(self) -> Dict[str, Any]:
        """Generate a SOMA (Somatic Architecture) update proposal based on evidence."""
        if not self.review_history:
            return {
                "proposal_id": "soma-proposal-000",
                "status": "no_data",
                "message": "Insufficient evidence for SOMA update proposal",
            }

        latest_review = self.review_history[-1]
        kpis = latest_review["engineering_kpis"]
        spec_recommendations = latest_review["specification_recommendations"]

        # Determine if SOMA update is recommended
        update_recommended = (
            kpis["recurring_architecture_issues"] > 0
            or len(spec_recommendations) > 0
            or kpis["architecture_health_score"] < 80
        )

        proposal = {
            "proposal_id": f"soma-proposal-{len(self.review_history):03d}",
            "timestamp": latest_review["timestamp"],
            "current_health_score": latest_review["architecture_health_score"],
            "update_recommended": update_recommended,
            "evidence_summary": {
                "total_executions_analyzed": latest_review["total_executions_analyzed"],
                "self_healing_success_rate": kpis["self_healing_success_rate"],
                "recurring_architecture_issues": kpis["recurring_architecture_issues"],
                "specification_improvements": kpis["specification_improvements"],
            },
            "proposed_changes": [],
        }

        if update_recommended:
            # Generate proposed changes from recommendations
            for recommendation in spec_recommendations:
                proposal["proposed_changes"].append(
                    {
                        "change_type": recommendation["type"],
                        "description": recommendation["recommended_action"],
                        "priority": recommendation["priority"],
                        "evidence": {
                            "issue": recommendation["issue"],
                            "occurrences": recommendation["occurrences"],
                        },
                    }
                )

            proposal["status"] = "ready_for_review"
            proposal["recommendation"] = "Approve SOMA update to address recurring architecture issues"
        else:
            proposal["status"] = "no_update_needed"
            proposal["recommendation"] = "Current SOMA specification is performing well"

        return proposal

    def get_cognitive_monitoring_dashboard(self) -> Dict[str, Any]:
        """Generate a cognitive monitoring dashboard with engineering KPIs."""
        if not self.review_history:
            return self._get_empty_dashboard()

        latest_review = self.review_history[-1]
        kpis = latest_review["engineering_kpis"]

        return {
            "dashboard_id": f"cognitive-monitoring-{len(self.review_history)}",
            "timestamp": latest_review["timestamp"],
            "metrics": {
                "monkey_brain": {
                    "self_healing_success": f"{kpis['self_healing_success_rate']}%",
                    "average_fix_time": f"{kpis['average_fix_time_ms']}ms",
                    "autonomous_fixes": kpis["autonomous_fixes"],
                    "regression_rate": f"{kpis['regression_rate']}%",
                },
                "architecture": {
                    "health_score": latest_review["architecture_health_score"],
                    "recurring_issues": kpis["recurring_architecture_issues"],
                    "technical_debt_trend": f"{kpis['technical_debt_trend']}%",
                    "specification_improvements": kpis["specification_improvements"],
                },
                "engineering": {
                    "human_intervention": f"{kpis['human_intervention_rate']}%",
                    "autonomy_level": f"{100 - kpis['human_intervention_rate']}%",
                    "executions_analyzed": latest_review["total_executions_analyzed"],
                    "evidence_quality": min(100, latest_review["architecture_health_score"] + 20),
                },
            },
            "top_findings": latest_review["top_findings"],
            "top_recommendations": latest_review["top_recommendations"],
            "status": "operational",
            "next_review_recommended": kpis["recurring_architecture_issues"] > 0,
        }

    def _get_empty_dashboard(self) -> Dict[str, Any]:
        """Get an empty dashboard template."""
        return {
            "dashboard_id": "cognitive-monitoring-000",
            "timestamp": "",
            "metrics": {
                "monkey_brain": {
                    "self_healing_success": "0.0%",
                    "average_fix_time": "0ms",
                    "autonomous_fixes": 0,
                    "regression_rate": "0.0%",
                },
                "architecture": {
                    "health_score": 50.0,
                    "recurring_issues": 0,
                    "technical_debt_trend": "0%",
                    "specification_improvements": 0,
                },
                "engineering": {
                    "human_intervention": "0.0%",
                    "autonomy_level": "100%",
                    "executions_analyzed": 0,
                    "evidence_quality": 50,
                },
            },
            "top_findings": [],
            "top_recommendations": [],
            "status": "no_data",
            "next_review_recommended": False,
        }
