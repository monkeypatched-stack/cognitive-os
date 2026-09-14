"""SolverMesh — orchestrates all registered solvers in the Predict phase.

Responsibilities:
  - Maintain a registry of ISolver instances.
  - Route each problem dict to the highest-confidence solver (can_solve score).
  - Dispatch a batch of problems, returning one SolverResult per problem.
"""

from __future__ import annotations

import asyncio
import logging

from src.monkey_brain.kernel.predict.base import ISolver, SolverResult

logger = logging.getLogger(__name__)


class SolverMesh:
    """Registry + dispatcher for all Predict-phase solvers."""

    def __init__(self) -> None:
        self._solvers: list[ISolver] = []

    def register(self, solver: ISolver) -> "SolverMesh":
        self._solvers.append(solver)
        return self

    def best_solver(self, problem: dict) -> ISolver | None:
        if not self._solvers:
            mesh = build_default_mesh()
            self._solvers = mesh._solvers
        scored = [(s.can_solve(problem), s) for s in self._solvers]
        best_score, best = max(scored, key=lambda x: x[0])
        return best if best_score > 0.0 else None

    def select_solver(self, problem: dict) -> ISolver | None:
        """Alias for best_solver."""
        return self.best_solver(problem)

    async def dispatch(self, problem: dict) -> SolverResult | None:
        solver = self.best_solver(problem)
        if solver is None:
            logger.debug("[mesh] no solver for problem type=%r", problem.get("type"))
            return None
        try:
            result = await solver.solve(problem)
            logger.debug("[mesh] %s → confidence=%.2f", solver.name, result.confidence)
            return result
        except Exception as e:
            logger.debug("[mesh] %s failed: %s", solver.name, e)
            return None

    async def dispatch_all(self, problems: list[dict]) -> list[SolverResult | None]:
        return list(await asyncio.gather(*(self.dispatch(p) for p in problems)))

    async def solve(self, problem: dict) -> SolverResult | None:
        """Solve a single problem. Auto-registers default solvers if mesh is empty."""
        if not self._solvers:
            mesh = build_default_mesh()
            self._solvers = mesh._solvers
        return await self.dispatch(problem)

    def get_jepa(self):
        """Return the JEPA solver if registered, else None."""
        for solver in self._solvers:
            if hasattr(solver, "solver_class") and solver.solver_class.value == "jepa":
                return solver
        return None


def build_default_mesh() -> SolverMesh:
    """Construct a SolverMesh with all available solvers registered."""
    from src.monkey_brain.kernel.predict.graph_solver.graph import GraphSolver
    from src.monkey_brain.kernel.predict.constraint.constraint import ConstraintSolver
    from src.monkey_brain.kernel.predict.constraint.sat import SATSolver
    from src.monkey_brain.kernel.predict.constraint.rules import RuleEngineSolver
    from src.monkey_brain.kernel.predict.model_checker.model_checker import (
        ModelCheckerSolver,
    )
    from src.monkey_brain.kernel.predict.optimizer.optimizer import OptimizerSolver
    from src.monkey_brain.kernel.predict.mcts.monte_carlo import MonteCarloSolver

    mesh = SolverMesh()
    mesh.register(GraphSolver())
    mesh.register(SATSolver())
    mesh.register(ConstraintSolver())
    mesh.register(RuleEngineSolver())
    mesh.register(ModelCheckerSolver())
    mesh.register(OptimizerSolver())
    mesh.register(MonteCarloSolver())

    # JEPA is optional — numpy dependency; skip gracefully on import failure
    try:
        from src.monkey_brain.kernel.predict.jepa.jepa import JEPAWorldModel

        mesh.register(JEPAWorldModel())
    except Exception as e:
        logger.debug("[mesh] JEPA not available: %s", e)

    return mesh
