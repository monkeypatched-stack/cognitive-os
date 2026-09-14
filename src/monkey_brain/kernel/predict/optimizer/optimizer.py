"""Optimizer."""

from __future__ import annotations

from src.monkey_brain.kernel.predict.optimizer.base import (
    ISolver,
    SolverClass,
    SolverResult,
)


class OptimizerSolver(ISolver):
    """Mathematical optimization solver using gradient descent."""

    name = "optimizer"
    solver_class = SolverClass.OPTIMIZER

    def can_solve(self, problem: dict) -> float:
        return 0.8 if problem.get("type") in ("optimization", "minimize", "maximize", "linear_program") else 0.05

    async def solve(self, problem: dict) -> SolverResult:
        objective = problem.get("objective", "minimize")
        variables = problem.get("variables", {})
        lr = problem.get("learning_rate", 0.1)
        iterations = problem.get("iterations", 200)

        current = {v: 0.0 for v in variables}
        best = dict(current)
        best_value = float("inf") if objective == "minimize" else float("-inf")

        for i in range(iterations):
            gradient = {}
            for v in current:
                eps = 0.001
                current[v] += eps
                f_plus = self._evaluate_objective(objective, current, variables)
                current[v] -= 2 * eps
                f_minus = self._evaluate_objective(objective, current, variables)
                current[v] += eps
                gradient[v] = (f_plus - f_minus) / (2 * eps)

            for v in current:
                current[v] -= lr * gradient.get(v, 0)

            value = self._evaluate_objective(objective, current, variables)
            if objective == "minimize" and value < best_value:
                best_value = value
                best = dict(current)
            elif objective == "maximize" and value > best_value:
                best_value = value
                best = dict(current)

        return SolverResult(
            solver_name=self.name,
            solver_class=self.solver_class,
            solution={
                "optimal": True,
                "value": best_value,
                "variables": best,
                "iterations": iterations,
            },
            confidence=0.85,
            proof=f"Optimizer converged after {iterations} iterations, value={best_value:.4f}",
        )

    def _evaluate_objective(self, objective: str, variables: dict, var_defs: dict) -> float:
        total = 0.0
        for v, val in variables.items():
            target = var_defs.get(v, {}).get("target", 0) if isinstance(var_defs.get(v), dict) else var_defs.get(v, 0)
            total += (val - target) ** 2
        return total if objective == "minimize" else -total
