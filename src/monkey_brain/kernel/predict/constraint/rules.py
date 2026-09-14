from __future__ import annotations

from src.monkey_brain.kernel.predict.constraint.base import (
    ISolver,
    SolverClass,
    SolverResult,
)


class RuleEngineSolver(ISolver):
    """Deterministic rule-based solver."""

    name = "rule_engine"
    solver_class = SolverClass.RULE_ENGINE

    def can_solve(self, problem: dict) -> float:
        return 0.9 if problem.get("type") == "rule_check" else 0.1

    async def solve(self, problem: dict) -> SolverResult:
        rules = problem.get("rules", [])
        violations = []
        for rule in rules:
            if not rule.get("satisfied", True):
                violations.append(rule.get("description", "rule violated"))
        return SolverResult(
            solver_name=self.name,
            solver_class=self.solver_class,
            solution={"violations": violations, "satisfied": len(violations) == 0},
            confidence=0.95,
            proof=f"{len(violations)} violations found",
        )
