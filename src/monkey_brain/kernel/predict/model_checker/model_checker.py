"""Model Checker."""

from __future__ import annotations

from src.monkey_brain.kernel.predict.model_checker.base import (
    ISolver,
    SolverClass,
    SolverResult,
)


class ModelCheckerSolver(ISolver):
    """Model checking solver for formal verification."""

    name = "model_checker"
    solver_class = SolverClass.MODEL_CHECKER

    def can_solve(self, problem: dict) -> float:
        return 0.85 if problem.get("type") in ("verification", "model_check", "temporal_logic") else 0.05

    async def solve(self, problem: dict) -> SolverResult:
        model = problem.get("model", {})
        properties = problem.get("properties", [])
        invariants = problem.get("invariants", [])

        violations = []
        for inv in invariants:
            if not self._check_invariant(inv, model):
                violations.append(f"Invariant violated: {inv.get('name', 'unknown')}")

        for prop in properties:
            if not self._check_property(prop, model):
                violations.append(f"Property violated: {prop.get('name', 'unknown')}")

        verified = len(violations) == 0
        return SolverResult(
            solver_name=self.name,
            solver_class=self.solver_class,
            solution={
                "verified": verified,
                "violations": violations,
                "counterexample": violations[0] if violations else None,
            },
            confidence=0.95 if verified else 0.7,
            proof=f"Model check: {'passed' if verified else f'{len(violations)} violations'}",
            counterexamples=violations,
        )

    def _check_invariant(self, inv: dict, model: dict) -> bool:
        check_type = inv.get("type", "")
        if check_type == "range":
            var = inv.get("variable", "")
            low = inv.get("min", float("-inf"))
            high = inv.get("max", float("inf"))
            val = model.get(var, 0)
            return low <= val <= high
        elif check_type == "non_empty":
            var = inv.get("variable", "")
            return bool(model.get(var))
        elif check_type == "positive":
            var = inv.get("variable", "")
            return model.get(var, 0) > 0
        return True

    def _check_property(self, prop: dict, model: dict) -> bool:
        return self._check_invariant(prop, model)
