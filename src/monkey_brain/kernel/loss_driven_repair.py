"""Loss Driven Repair — repair engine for the cognitive kernel.

This is a simplified version focused on the cognitive cycle's needs,
different from the cortex.loss_driven_repair.py which operates on prompts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.config import REPAIR_LOSS_THRESHOLD


@dataclass
class RepairReport:
    """Result of a repair operation."""

    initial_loss: float = 0.0
    final_loss: float = 0.0
    total_iterations: int = 0
    convergence_reason: str = ""
    repairs_applied: list[str] = field(default_factory=list)


class LossDrivenRepair:
    """Repair engine that triggers when loss exceeds threshold."""

    def __init__(self, loss_threshold: float = REPAIR_LOSS_THRESHOLD, max_iterations: int = 10):
        self.loss_threshold = loss_threshold
        self.max_iterations = max_iterations

    def run(
        self,
        initial_loss: float,
        loss_context: dict[str, Any],
        loss_terms: dict[str, float],
    ) -> RepairReport:
        """Run repair process when loss exceeds threshold.

        Args:
            initial_loss: The initial composite loss value
            loss_context: Context information for repair
            loss_terms: Individual loss components

        Returns:
            RepairReport with results
        """
        if initial_loss <= self.loss_threshold:
            return RepairReport(
                initial_loss=initial_loss,
                final_loss=initial_loss,
                total_iterations=0,
                convergence_reason="loss_below_threshold",
            )

        # Simple repair strategy: identify dominant loss term and apply repair
        dominant_term = self._identify_dominant_term(loss_terms)
        repairs = self._generate_repairs(dominant_term, loss_context)

        # Simulate repair effect (in a real system, this would actually modify the graph)
        final_loss = max(0.0, initial_loss * 0.7)  # 30% improvement

        return RepairReport(
            initial_loss=initial_loss,
            final_loss=final_loss,
            total_iterations=1,
            convergence_reason="single_iteration_repair",
            repairs_applied=repairs,
        )

    def _identify_dominant_term(self, loss_terms: dict[str, float]) -> str:
        """Identify which loss term is dominant."""
        if not loss_terms:
            return "unknown"
        return max(loss_terms.items(), key=lambda x: x[1])[0]

    def _generate_repairs(self, dominant_term: str, context: dict[str, Any]) -> list[str]:
        """Generate repair actions for the dominant loss term."""
        # Simple mapping of loss terms to repair actions
        repair_mapping = {
            "L_S": ["adjust_world_state_prediction"],
            "L_B": ["improve_belief_confidence"],
            "L_A": ["correct_affordance_prediction"],
            "L_M": ["update_mesh_state"],
            "L_K": ["retrieve_additional_knowledge"],
            "L_C": ["fix_constraint_violations"],
            "L_G": ["improve_goal_progress"],
        }

        return repair_mapping.get(dominant_term, ["general_repair"])

    def summary(self) -> dict[str, Any]:
        """Get summary of repair engine state."""
        return {
            "type": "loss_driven_repair",
            "loss_threshold": self.loss_threshold,
            "status": "active",
        }
