"""Sat."""

from __future__ import annotations

from src.monkey_brain.kernel.predict.constraint.base import (
    ISolver,
    SolverClass,
    SolverResult,
)


class SATSolver(ISolver):
    """SAT solver using DPLL for boolean satisfiability.

    Encodes constraints as CNF clauses and solves via DPLL.
    """

    name = "sat"
    solver_class = SolverClass.SAT_SMT

    def can_solve(self, problem: dict) -> float:
        return 0.9 if problem.get("type") in ("satisfiability", "constraint_logic") else 0.05

    async def solve(self, problem: dict) -> SolverResult:
        clauses = problem.get("clauses", [])
        variables = problem.get("variables", [])

        if not clauses:
            return SolverResult(
                solver_name=self.name,
                solver_class=self.solver_class,
                solution={"satisfiable": True, "model": {}},
                confidence=0.99,
                proof="No constraints — trivially satisfiable",
            )

        assignment = {}
        satisfiable, model = self._dpll(clauses, variables, assignment)

        return SolverResult(
            solver_name=self.name,
            solver_class=self.solver_class,
            solution={"satisfiable": satisfiable, "model": model},
            confidence=0.99 if satisfiable else 0.95,
            proof=f"DPLL: {'SAT' if satisfiable else 'UNSAT'} with {len(clauses)} clauses",
        )

    def _dpll(self, clauses, variables, assignment):
        if not clauses:
            return True, dict(assignment)
        if any(self._evaluate_clause(clause, assignment) is False for clause in clauses):
            return False, {}

        for clause in clauses:
            unassigned = [l for l in clause if abs(l) not in assignment]
            if len(unassigned) == 1:
                lit = unassigned[0]
                var = abs(lit)
                val = lit > 0
                assignment[var] = val
                result, model = self._dpll(clauses, variables, assignment)
                if result:
                    return True, model
                del assignment[var]
                return False, {}

        unassigned_vars = [v for v in variables if v not in assignment]
        if not unassigned_vars:
            return True, dict(assignment)

        var = unassigned_vars[0]
        for val in [True, False]:
            assignment[var] = val
            result, model = self._dpll(clauses, variables, assignment)
            if result:
                return True, model
            del assignment[var]
        return False, {}

    def _evaluate_clause(self, clause, assignment):
        for lit in clause:
            var = abs(lit)
            if var in assignment:
                val = assignment[var]
                if (lit > 0 and val) or (lit < 0 and not val):
                    return True
        unassigned = [l for l in clause if abs(l) not in assignment]
        return None if unassigned else False
