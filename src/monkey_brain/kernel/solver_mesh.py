"""Solver Mesh — convenience re-exports for all Predict-phase solvers.

This module provides a single import point for all solver classes.
"""

from __future__ import annotations

from src.monkey_brain.kernel.predict.solver_mesh import SolverMesh
from src.monkey_brain.kernel.predict.graph_solver.graph import GraphSolver
from src.monkey_brain.kernel.predict.constraint.sat import SATSolver
from src.monkey_brain.kernel.predict.constraint.constraint import ConstraintSolver
from src.monkey_brain.kernel.predict.model_checker.model_checker import (
    ModelCheckerSolver,
)
from src.monkey_brain.kernel.predict.optimizer.optimizer import OptimizerSolver
from src.monkey_brain.kernel.predict.mcts.monte_carlo import MonteCarloSolver
from src.monkey_brain.kernel.predict.jepa.jepa import JEPAWorldModel
from src.monkey_brain.kernel.predict.base import SolverResult, SolverClass


class LLMSolver:
    """Simple LLM-based solver for general reasoning tasks."""

    name = "llm"
    solver_class = SolverClass.GRAPH  # Default to GRAPH for compatibility

    def can_solve(self, problem: dict) -> float:
        return 0.5

    async def solve(self, problem: dict) -> SolverResult:
        return SolverResult(
            solver_name=self.name,
            solver_class=self.solver_class,
            solution={"reasoning": "LLM solver reasoning placeholder", "answer": None},
            confidence=0.5,
            proof="LLM-based reasoning",
        )


__all__ = [
    "GraphSolver",
    "SATSolver",
    "ConstraintSolver",
    "ModelCheckerSolver",
    "OptimizerSolver",
    "MonteCarloSolver",
    "JEPAWorldModel",
    "LLMSolver",
    "SolverMesh",
    "SolverResult",
    "SolverClass",
]
