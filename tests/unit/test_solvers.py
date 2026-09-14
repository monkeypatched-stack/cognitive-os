"""Deterministic Solver Verification Suite.

Proves correctness of Graph, SAT, Constraint, and ModelChecker solvers
with deterministic, reproducible tests. Each test asserts a specific
mathematical property — the solver is guilty until proven correct.
"""

import asyncio
import sys
import os
import math

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.kernel.solver_mesh import (
    GraphSolver,
    SATSolver,
    ConstraintSolver,
    ModelCheckerSolver,
    SolverMesh,
    SolverClass,
)


def _r(c):
    l = asyncio.new_event_loop()
    try:
        return l.run_until_complete(c)
    finally:
        l.close()


graph = GraphSolver()
sat = SATSolver()
constraint = ConstraintSolver()
mc = ModelCheckerSolver()


# ═══════════════════════════════════════════════════════════════
# GRAPH SOLVER VERIFICATION
# ═══════════════════════════════════════════════════════════════


def test_graph_reachability_positive():
    r = _r(
        graph.solve(
            {
                "graph": {"a": ["b"], "b": ["c"]},
                "query": {"source": "a", "target": "c", "check": "reachability"},
            }
        )
    )
    assert r.solution["reachable"] is True
    assert r.solution["path"] == ["a", "b", "c"]


def test_graph_reachability_negative():
    r = _r(
        graph.solve(
            {
                "graph": {"a": ["b"], "c": ["d"]},
                "query": {"source": "a", "target": "d", "check": "reachability"},
            }
        )
    )
    assert r.solution["reachable"] is False
    assert r.solution["path"] == []


def test_graph_reachability_self():
    r = _r(
        graph.solve(
            {
                "graph": {"a": []},
                "query": {"source": "a", "target": "a", "check": "reachability"},
            }
        )
    )
    assert r.solution["reachable"] is True
    assert r.solution["path"] == ["a"]


def test_graph_reachability_diamond():
    r = _r(
        graph.solve(
            {
                "graph": {"a": ["b", "c"], "b": ["d"], "c": ["d"]},
                "query": {"source": "a", "target": "d", "check": "reachability"},
            }
        )
    )
    assert r.solution["reachable"] is True


def test_graph_reachability_missing_source():
    r = _r(
        graph.solve(
            {
                "graph": {"a": ["b"]},
                "query": {"source": "x", "target": "b", "check": "reachability"},
            }
        )
    )
    assert r.solution["reachable"] is False


def test_graph_reachability_empty_graph():
    r = _r(
        graph.solve(
            {
                "graph": {},
                "query": {"source": "x", "target": "y", "check": "reachability"},
            }
        )
    )
    assert r.solution["reachable"] is False


def test_graph_cycle_self_loop():
    r = _r(graph.solve({"graph": {"a": ["a"]}, "query": {"check": "cycle_check"}}))
    assert r.solution["has_cycle"] is True


def test_graph_cycle_two_node():
    r = _r(graph.solve({"graph": {"a": ["b"], "b": ["a"]}, "query": {"check": "cycle_check"}}))
    assert r.solution["has_cycle"] is True


def test_graph_cycle_three_node():
    r = _r(
        graph.solve(
            {
                "graph": {"a": ["b"], "b": ["c"], "c": ["a"]},
                "query": {"check": "cycle_check"},
            }
        )
    )
    assert r.solution["has_cycle"] is True


def test_graph_no_cycle_dag():
    r = _r(
        graph.solve(
            {
                "graph": {"a": ["b"], "b": ["c"], "c": []},
                "query": {"check": "cycle_check"},
            }
        )
    )
    assert r.solution["has_cycle"] is False


def test_graph_no_cycle_empty():
    r = _r(graph.solve({"graph": {}, "query": {"check": "cycle_check"}}))
    assert r.solution["has_cycle"] is False


def test_graph_no_cycle_disconnected():
    r = _r(graph.solve({"graph": {"a": ["b"], "c": ["d"]}, "query": {"check": "cycle_check"}}))
    assert r.solution["has_cycle"] is False


def test_graph_topo_linear():
    r = _r(
        graph.solve(
            {
                "graph": {"a": ["b"], "b": ["c"], "c": []},
                "query": {"check": "topological"},
            }
        )
    )
    assert r.solution["is_dag"] is True
    order = r.solution["topological_order"]
    assert order.index("a") < order.index("b")
    assert order.index("b") < order.index("c")


def test_graph_topo_diamond():
    r = _r(
        graph.solve(
            {
                "graph": {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []},
                "query": {"check": "topological"},
            }
        )
    )
    assert r.solution["is_dag"] is True
    order = r.solution["topological_order"]
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_graph_topo_cycle_returns_none():
    r = _r(graph.solve({"graph": {"a": ["a"]}, "query": {"check": "topological"}}))
    assert r.solution["is_dag"] is False
    assert r.solution["topological_order"] is None


def test_graph_topo_empty():
    r = _r(graph.solve({"graph": {}, "query": {"check": "topological"}}))
    assert r.solution["is_dag"] is True
    assert r.solution["topological_order"] == []


def test_graph_dispatch():
    m = SolverMesh()
    r = _r(
        m.solve(
            {
                "type": "reachability",
                "graph": {"a": ["b"]},
                "query": {"source": "a", "target": "b", "check": "reachability"},
            }
        )
    )
    assert r.solver_name == "graph"


# ═══════════════════════════════════════════════════════════════
# SAT SOLVER VERIFICATION (DPLL)
# ═══════════════════════════════════════════════════════════════


def test_sat_empty_clauses():
    r = _r(sat.solve({"clauses": [], "variables": []}))
    assert r.solution["satisfiable"] is True


def test_sat_single_literal():
    r = _r(sat.solve({"clauses": [[1]], "variables": [1]}))
    assert r.solution["satisfiable"] is True
    assert r.solution["model"][1] is True


def test_sat_unit_propagation():
    r = _r(sat.solve({"clauses": [[1]], "variables": [1]}))
    assert r.solution["satisfiable"] is True
    assert r.solution["model"] == {1: True}


def test_sat_chain():
    r = _r(sat.solve({"clauses": [[1], [-1, 2], [-2, 3]], "variables": [1, 2, 3]}))
    assert r.solution["satisfiable"] is True
    assert r.solution["model"] == {1: True, 2: True, 3: True}


def test_sat_contradiction():
    r = _r(sat.solve({"clauses": [[1], [-1]], "variables": [1]}))
    assert r.solution["satisfiable"] is False


def test_sat_contradiction_chain():
    r = _r(sat.solve({"clauses": [[1], [-1, 2], [-2, 3], [-3]], "variables": [1, 2, 3]}))
    assert r.solution["satisfiable"] is False


def test_sat_two_variables():
    r = _r(sat.solve({"clauses": [[1, 2]], "variables": [1, 2]}))
    assert r.solution["satisfiable"] is True
    model = r.solution["model"]
    assert model[1] is True or model[2] is True


def test_sat_force_both():
    r = _r(sat.solve({"clauses": [[1, 2], [1, -2]], "variables": [1, 2]}))
    assert r.solution["satisfiable"] is True
    assert r.solution["model"][1] is True


def test_sat_3sat():
    r = _r(sat.solve({"clauses": [[1, 2, 3], [-1, 2, 3], [1, -2, 3]], "variables": [1, 2, 3]}))
    assert r.solution["satisfiable"] is True


def test_sat_pigeonhole_2():
    # 2 pigeons, 1 hole: (x1) AND (x2) AND (NOT x1 OR NOT x2) — UNSAT
    r = _r(sat.solve({"clauses": [[1], [2], [-1, -2]], "variables": [1, 2]}))
    assert r.solution["satisfiable"] is False


def test_sat_large_unsat():
    clauses = [[i] for i in range(1, 51)] + [[-i] for i in range(1, 51)]
    r = _r(sat.solve({"clauses": clauses, "variables": list(range(1, 51))}))
    assert r.solution["satisfiable"] is False


def test_sat_large_sat():
    clauses = [[i, -(i + 1)] for i in range(1, 50)] + [[50]]
    r = _r(sat.solve({"clauses": clauses, "variables": list(range(1, 51))}))
    assert r.solution["satisfiable"] is True
    model = r.solution["model"]
    for i in range(1, 51):
        assert i in model


def test_sat_dispatch():
    m = SolverMesh()
    r = _r(m.solve({"type": "satisfiability", "clauses": [[1]], "variables": [1]}))
    assert r.solver_name == "sat"


# ═══════════════════════════════════════════════════════════════
# CONSTRAINT SOLVER VERIFICATION (Backtracking)
# ═══════════════════════════════════════════════════════════════


def test_constraint_basic():
    r = _r(
        constraint.solve(
            {
                "variables": {"x": [1, 2], "y": [3, 4]},
                "constraints": [{"type": "all_different", "variables": ["x", "y"]}],
            }
        )
    )
    assert r.solution["feasible"] is True
    a = r.solution["assignments"]
    assert a["x"] != a["y"]


def test_constraint_infeasible():
    r = _r(
        constraint.solve(
            {
                "variables": {"x": [1], "y": [1]},
                "constraints": [{"type": "all_different", "variables": ["x", "y"]}],
            }
        )
    )
    assert r.solution["feasible"] is False


def test_constraint_three_vars():
    r = _r(
        constraint.solve(
            {
                "variables": {"x": [1, 2, 3], "y": [1, 2, 3], "z": [1, 2, 3]},
                "constraints": [{"type": "all_different", "variables": ["x", "y", "z"]}],
            }
        )
    )
    assert r.solution["feasible"] is True
    a = r.solution["assignments"]
    assert len(set(a.values())) == 3


def test_constraint_sum():
    r = _r(
        constraint.solve(
            {
                "variables": {"x": [1, 2, 3], "y": [1, 2, 3]},
                "constraints": [{"type": "sum_equals", "variables": ["x", "y"], "target": 5}],
            }
        )
    )
    assert r.solution["feasible"] is True
    assert r.solution["assignments"]["x"] + r.solution["assignments"]["y"] == 5


def test_constraint_less_than():
    r = _r(
        constraint.solve(
            {
                "variables": {"x": [1, 5], "y": [1, 5]},
                "constraints": [{"type": "less_than", "a": "x", "b": "y"}],
            }
        )
    )
    assert r.solution["feasible"] is True
    assert r.solution["assignments"]["x"] < r.solution["assignments"]["y"]


def test_constraint_empty_vars():
    r = _r(
        constraint.solve(
            {
                "variables": {},
                "constraints": [],
            }
        )
    )
    assert r.solution["feasible"] is True


def test_constraint_single_var():
    r = _r(
        constraint.solve(
            {
                "variables": {"x": [1, 2, 3]},
                "constraints": [],
            }
        )
    )
    assert r.solution["feasible"] is True
    assert r.solution["assignments"]["x"] in [1, 2, 3]


def test_constraint_dispatch():
    m = SolverMesh()
    r = _r(m.solve({"type": "constraint", "variables": {"x": [1]}, "constraints": []}))
    assert r.solver_name == "constraint"


# ═══════════════════════════════════════════════════════════════
# MODEL CHECKER SOLVER VERIFICATION
# ═══════════════════════════════════════════════════════════════


def test_mc_positive():
    r = _r(
        mc.solve(
            {
                "model": {"x": 5},
                "invariants": [{"type": "positive", "variable": "x"}],
            }
        )
    )
    assert r.solution["verified"] is True
    assert len(r.solution["violations"]) == 0


def test_mc_negative():
    r = _r(
        mc.solve(
            {
                "model": {"x": -1},
                "invariants": [{"type": "positive", "variable": "x"}],
            }
        )
    )
    assert r.solution["verified"] is False
    assert len(r.solution["violations"]) == 1


def test_mc_zero_fails_positive():
    r = _r(
        mc.solve(
            {
                "model": {"x": 0},
                "invariants": [{"type": "positive", "variable": "x"}],
            }
        )
    )
    assert r.solution["verified"] is False


def test_mc_range_ok():
    r = _r(
        mc.solve(
            {
                "model": {"x": 5},
                "invariants": [{"type": "range", "variable": "x", "min": 0, "max": 10}],
            }
        )
    )
    assert r.solution["verified"] is True


def test_mc_range_low():
    r = _r(
        mc.solve(
            {
                "model": {"x": -1},
                "invariants": [{"type": "range", "variable": "x", "min": 0, "max": 10}],
            }
        )
    )
    assert r.solution["verified"] is False


def test_mc_range_high():
    r = _r(
        mc.solve(
            {
                "model": {"x": 11},
                "invariants": [{"type": "range", "variable": "x", "min": 0, "max": 10}],
            }
        )
    )
    assert r.solution["verified"] is False


def test_mc_range_boundary_low():
    r = _r(
        mc.solve(
            {
                "model": {"x": 0},
                "invariants": [{"type": "range", "variable": "x", "min": 0, "max": 10}],
            }
        )
    )
    assert r.solution["verified"] is True


def test_mc_range_boundary_high():
    r = _r(
        mc.solve(
            {
                "model": {"x": 10},
                "invariants": [{"type": "range", "variable": "x", "min": 0, "max": 10}],
            }
        )
    )
    assert r.solution["verified"] is True


def test_mc_non_empty():
    r = _r(
        mc.solve(
            {
                "model": {"x": "hello"},
                "invariants": [{"type": "non_empty", "variable": "x"}],
            }
        )
    )
    assert r.solution["verified"] is True


def test_mc_non_empty_empty_string():
    r = _r(
        mc.solve(
            {
                "model": {"x": ""},
                "invariants": [{"type": "non_empty", "variable": "x"}],
            }
        )
    )
    assert r.solution["verified"] is False


def test_mc_non_empty_missing():
    r = _r(
        mc.solve(
            {
                "model": {},
                "invariants": [{"type": "non_empty", "variable": "x"}],
            }
        )
    )
    assert r.solution["verified"] is False


def test_mc_multiple_invariants():
    r = _r(
        mc.solve(
            {
                "model": {"x": 5, "y": "active"},
                "invariants": [
                    {"type": "positive", "variable": "x"},
                    {"type": "non_empty", "variable": "y"},
                ],
            }
        )
    )
    assert r.solution["verified"] is True
    assert len(r.solution["violations"]) == 0


def test_mc_multiple_violations():
    r = _r(
        mc.solve(
            {
                "model": {"x": -1, "y": ""},
                "invariants": [
                    {"type": "positive", "variable": "x"},
                    {"type": "non_empty", "variable": "y"},
                ],
            }
        )
    )
    assert r.solution["verified"] is False
    assert len(r.solution["violations"]) == 2


def test_mc_no_invariants():
    r = _r(
        mc.solve(
            {
                "model": {"x": 1},
                "invariants": [],
            }
        )
    )
    assert r.solution["verified"] is True


def test_mc_missing_variable():
    r = _r(
        mc.solve(
            {
                "model": {},
                "invariants": [{"type": "positive", "variable": "x"}],
            }
        )
    )
    assert r.solution["verified"] is False


def test_mc_dispatch():
    m = SolverMesh()
    r = _r(
        m.solve(
            {
                "type": "verification",
                "model": {"x": 1},
                "invariants": [{"type": "positive", "variable": "x"}],
            }
        )
    )
    assert r.solver_name == "model_checker"


def test_mc_counterexample():
    r = _r(
        mc.solve(
            {
                "model": {"x": -5},
                "invariants": [{"type": "positive", "variable": "x"}],
            }
        )
    )
    assert len(r.counterexamples) > 0


def test_mc_proof():
    r_pass = _r(mc.solve({"model": {"x": 1}, "invariants": [{"type": "positive", "variable": "x"}]}))
    assert "passed" in r_pass.proof.lower()
    r_fail = _r(mc.solve({"model": {"x": -1}, "invariants": [{"type": "positive", "variable": "x"}]}))
    assert "violations" in r_fail.proof.lower()


# ═══════════════════════════════════════════════════════════════
# CROSS-SOLVER PROPERTIES
# ═══════════════════════════════════════════════════════════════


def test_mesh_selects_graph_for_reachability():
    m = SolverMesh()
    s = m.select_solver({"type": "reachability"})
    assert s.solver_class == SolverClass.GRAPH


def test_mesh_selects_sat_for_satisfiability():
    m = SolverMesh()
    s = m.select_solver({"type": "satisfiability"})
    assert s.solver_class == SolverClass.SAT_SMT


def test_mesh_selects_constraint():
    m = SolverMesh()
    s = m.select_solver({"type": "constraint"})
    assert s.solver_class == SolverClass.CONSTRAINT


def test_mesh_selects_mc_for_verification():
    m = SolverMesh()
    s = m.select_solver({"type": "verification"})
    assert s.solver_class == SolverClass.MODEL_CHECKER


def test_mesh_deterministic_results():
    m = SolverMesh()
    problem = {
        "type": "reachability",
        "graph": {"a": ["b"], "b": ["c"]},
        "query": {"source": "a", "target": "c", "check": "reachability"},
    }
    r1 = _r(m.solve(problem))
    r2 = _r(m.solve(problem))
    assert r1.solution == r2.solution


# ═══════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════

ALL_TESTS = {k: v for k, v in globals().items() if k.startswith("test_") and callable(v)}

if __name__ == "__main__":
    ok = 0
    fail = 0
    failures = []
    for name, fn in sorted(ALL_TESTS.items()):
        try:
            fn()
            ok += 1
        except Exception as e:
            fail += 1
            failures.append(f"{name}: {e}")

    print(f"\n{'=' * 60}")
    print(f"DETERMINISTIC SOLVER VERIFICATION")
    print(f"{'=' * 60}")
    print(f"  Graph:     {sum(1 for n in ALL_TESTS if 'graph' in n)}/" + str(sum(1 for n in ALL_TESTS if "graph" in n)))
    print(f"  SAT:       {sum(1 for n in ALL_TESTS if 'sat' in n)}/" + str(sum(1 for n in ALL_TESTS if "sat" in n)))
    print(
        f"  Constraint:{sum(1 for n in ALL_TESTS if 'constraint' in n)}/"
        + str(sum(1 for n in ALL_TESTS if "constraint" in n))
    )
    print(f"  MC:        {sum(1 for n in ALL_TESTS if 'mc' in n)}/" + str(sum(1 for n in ALL_TESTS if "mc" in n)))
    print(
        f"  Cross:     {sum(1 for n in ALL_TESTS if 'mesh' in n or 'dispatch' in n)}/"
        + str(sum(1 for n in ALL_TESTS if "mesh" in n or "dispatch" in n))
    )
    print(f"{'=' * 60}")
    print(f"  Total:     {ok}/{ok + fail}")
    if failures:
        print(f"  FAILURES:")
        for f in failures:
            print(f"    {f}")
    else:
        print(f"  ALL CHECKS PASS")
    print(f"{'=' * 60}")
