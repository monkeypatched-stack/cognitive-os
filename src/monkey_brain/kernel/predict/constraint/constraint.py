"""Constraint."""

from __future__ import annotations

from src.monkey_brain.kernel.predict.constraint.base import (
    ISolver,
    SolverClass,
    SolverResult,
)


class ConstraintSolver(ISolver):
    """Constraint satisfaction solver using backtracking."""

    name = "constraint"
    solver_class = SolverClass.CONSTRAINT

    def can_solve(self, problem: dict) -> float:
        return 0.85 if problem.get("type") in ("constraint", "scheduling", "resource_allocation") else 0.05

    async def solve(self, problem: dict) -> SolverResult:
        variables = problem.get("variables", {})
        constraints = problem.get("constraints", [])

        assignment = {}
        feasible = self._backtrack(variables, constraints, assignment)

        return SolverResult(
            solver_name=self.name,
            solver_class=self.solver_class,
            solution={"feasible": feasible, "assignments": dict(assignment)},
            confidence=0.9 if feasible else 0.8,
            proof=f"Constraint solver: {'feasible' if feasible else 'infeasible'} with {len(constraints)} constraints",
        )

    def _backtrack(self, variables: dict, constraints: list, assignment: dict) -> bool:
        if len(assignment) == len(variables):
            return all(self._check_constraint(c, assignment) for c in constraints)

        for var, domain in variables.items():
            if var in assignment:
                continue
            for value in domain if isinstance(domain, list) else [domain]:
                assignment[var] = value
                if self._backtrack(variables, constraints, assignment):
                    return True
                del assignment[var]
            return False
        return True

    def _check_constraint(self, constraint: dict, assignment: dict) -> bool:
        ctype = constraint.get("type", "")
        if ctype == "all_different":
            values = [assignment.get(v) for v in constraint.get("variables", []) if v in assignment]
            return len(values) == len(set(str(v) for v in values))
        elif ctype == "sum_equals":
            variables = constraint.get("variables", [])
            target = constraint.get("target", 0)
            total = sum(assignment.get(v, 0) for v in variables if v in assignment)
            return total == target
        elif ctype == "less_than":
            a = assignment.get(constraint.get("a"), 0)
            b = assignment.get(constraint.get("b"), 0)
            return a < b
        return True
