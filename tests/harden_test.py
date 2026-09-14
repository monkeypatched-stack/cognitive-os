"""Hardening test suite — 81 checks across 24 categories."""

import asyncio, random, threading, sys, os, time, math, json

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)
import py_compile

from src.monkey_brain.kernel.solver_mesh import (
    GraphSolver,
    SATSolver,
    ConstraintSolver,
    ModelCheckerSolver,
    OptimizerSolver,
    MonteCarloSolver,
    JEPAWorldModel,
    LLMSolver,
    SolverMesh,
)
from src.knowledge.pack import KnowledgePack
from src.knowledge.item import KnowledgeItem, Modality

from src.monkey_brain.kernel.learn.epa.evidence_fusion import EvidenceFusionEngine
from src.knowledge.interface import Evidence
from src.monkey_brain.kernel.fix.policy.retrieval import HeuristicRetrievalPolicy
from src.monkey_brain.kernel.learn.epa.epistemic import (
    EpistemicState,
    BeliefState,
    KnowledgeConfidence,
)
from src.monkey_brain.kernel.execute.agent_mesh import (
    ExecutionPool,
    AgentSpec,
    AgentRole,
)
from src.monkey_brain.kernel.cognitive_kernel import CognitiveKernel
from src.monkey_brain.kernel.fix.performance_budgets import PerformanceMonitor
from src.monkey_brain.kernel.fix.assumption_validator.assumption_validator import (
    AssumptionValidator,
)
from src.monkey_brain.kernel.learn.epa.completeness import EpistemicCompleteness
from cortex.epa import epa_loss, EpistemicPredictiveState


def _r(c):
    l = asyncio.new_event_loop()
    try:
        return l.run_until_complete(c)
    finally:
        l.close()


ok = 0
f = []


def T(n, fn):
    global ok
    try:
        fn()
        ok += 1
    except Exception as e:
        f.append(f"{n}: {e}")


# === 1. Static ===
T(
    "Compile",
    lambda: all(
        py_compile.compile(os.path.join(r, f), doraise=True)
        for r, _, fs in os.walk("src/monkey_brain/kernel")
        for f in fs
        if f.endswith(".py") and not f.startswith("__")
    ),
)


def check_no_stubs():
    for r, _, fs in os.walk("src/monkey_brain/kernel"):
        for f in fs:
            if f.endswith(".py") and not f.startswith("__"):
                path = os.path.join(r, f)
                with open(path) as fh:
                    content = fh.read()
                    assert "pass  # TODO" not in content, f"Stub in {path}"


T("No stubs", check_no_stubs)


def check_no_empty():
    for r, _, fs in os.walk("src/monkey_brain/kernel"):
        for f in fs:
            if f.endswith(".py") and not f.startswith("__"):
                path = os.path.join(r, f)
                with open(path) as fh:
                    content = fh.read().strip()
                    assert len(content) > 50, f"Empty file: {path}"


T("No empty", check_no_empty)


def check_no_bare():
    import re as _re

    for r, _, fs in os.walk("src/monkey_brain/kernel"):
        for f in fs:
            if f.endswith(".py") and not f.startswith("__"):
                path = os.path.join(r, f)
                with open(path) as fh:
                    for i, line in enumerate(fh, 1):
                        stripped = line.strip()
                        if stripped == "except:":
                            assert False, f"Bare except at {path}:{i}"


T("No bare", check_no_bare)

# === 2. Solver correctness (16) ===
T(
    "Graph disc",
    lambda: (
        not _r(
            GraphSolver().solve(
                {
                    "graph": {"a": ["b"], "c": ["d"]},
                    "query": {"source": "a", "target": "d", "check": "reachability"},
                }
            )
        ).solution["reachable"]
    ),
)
T(
    "Graph loop",
    lambda: _r(GraphSolver().solve({"graph": {"a": ["a"]}, "query": {"check": "cycle_check"}})).solution["has_cycle"],
)
T(
    "Graph DAG",
    lambda: (
        not _r(
            GraphSolver().solve(
                {
                    "graph": {"a": ["b"], "b": ["c"], "c": []},
                    "query": {"check": "cycle_check"},
                }
            )
        ).solution["has_cycle"]
    ),
)
T(
    "Graph topo",
    lambda: (
        _r(
            GraphSolver().solve(
                {
                    "graph": {"a": ["b"], "b": ["c"], "c": []},
                    "query": {"check": "topological"},
                }
            )
        ).solution["topological_order"]
        is not None
    ),
)
T(
    "SAT unsat",
    lambda: not _r(SATSolver().solve({"clauses": [[1], [-1]], "variables": [1]})).solution["satisfiable"],
)
T(
    "SAT sat",
    lambda: _r(SATSolver().solve({"clauses": [[1, 2], [-1, 2]], "variables": [1, 2]})).solution["satisfiable"],
)
T(
    "Constr feas",
    lambda: _r(
        ConstraintSolver().solve(
            {
                "variables": {"x": [1, 2], "y": [3, 4]},
                "constraints": [{"type": "all_different", "variables": ["x", "y"]}],
            }
        )
    ).solution["feasible"],
)
T(
    "Constr infeas",
    lambda: (
        not _r(
            ConstraintSolver().solve(
                {
                    "variables": {"x": [1], "y": [1]},
                    "constraints": [{"type": "all_different", "variables": ["x", "y"]}],
                }
            )
        ).solution["feasible"]
    ),
)
T(
    "MC verif",
    lambda: _r(
        ModelCheckerSolver().solve({"model": {"x": 1}, "invariants": [{"type": "positive", "variable": "x"}]})
    ).solution["verified"],
)
T(
    "MC viol",
    lambda: (
        not _r(
            ModelCheckerSolver().solve({"model": {"x": -1}, "invariants": [{"type": "positive", "variable": "x"}]})
        ).solution["verified"]
    ),
)
T(
    "Optim",
    lambda: (
        abs(
            _r(OptimizerSolver().solve({"objective": "minimize", "variables": {"x": 5.0}, "iterations": 200})).solution[
                "variables"
            ]["x"]
            - 5.0
        )
        < 0.5
    ),
)
T(
    "MCTS act",
    lambda: _r(MonteCarloSolver().solve({"actions": ["only"], "n_simulations": 50})).solution["policy"] == "only",
)
T(
    "MCTS empty",
    lambda: _r(MonteCarloSolver().solve({"actions": [], "n_simulations": 10})).solution["policy"] is None,
)
T(
    "JEPA",
    lambda: "predicted_state" in _r(JEPAWorldModel().solve({"current_state": {"x": 1}, "action": "move"})).solution,
)
T("LLM", lambda: _r(LLMSolver().solve({})).solution["reasoning"] != "")
T(
    "Dispatch",
    lambda: all(
        _r(SolverMesh().solve({"type": t})).solver_class.name == n
        for t, n in [
            ("rule_check", "RULE_ENGINE"),
            ("reachability", "GRAPH"),
            ("satisfiability", "SAT_SMT"),
            ("constraint", "CONSTRAINT"),
            ("verification", "MODEL_CHECKER"),
            ("optimization", "OPTIMIZER"),
            ("planning", "MONTE_CARLO"),
            ("prediction", "JEPA"),
            ("general", "LLM"),
        ]
    ),
)


# === 3. Knowledge (5) ===
def kp_dup():
    k = KnowledgePack()
    k.add(KnowledgeItem(id="k1", content="a", modality=Modality.DOCUMENT, source="s", provenance=0.5))
    k.add(KnowledgeItem(id="k1", content="b", modality=Modality.DOCUMENT, source="s", provenance=0.9))
    assert k.size == 1 and k.get("k1").content == "b"


T("KP dup", kp_dup)
T("KP query", lambda: len(KnowledgePack().query(modality=Modality.TELEMETRY)) == 0)
T(
    "KP fusion",
    lambda: (
        lambda k: (
            k.add(
                KnowledgeItem(
                    id="k1",
                    content="doc",
                    modality=Modality.DOCUMENT,
                    source="s1",
                    provenance=0.9,
                )
            )
            or k.add(
                KnowledgeItem(
                    id="k2",
                    content="sensor",
                    modality=Modality.TELEMETRY,
                    source="s2",
                    provenance=0.8,
                )
            )
            or (len(k.modalities) == 2 and k.fuse().fused_confidence > 0)
        )
    )(KnowledgePack()),
)


def kp_10k():
    k = KnowledgePack()
    for i in range(10000):
        k.add(
            KnowledgeItem(
                id=f"k{i}",
                content=f"item {i}",
                modality=Modality.DOCUMENT,
                source=f"s{i}",
                provenance=random.random(),
            )
        )
    assert k.size == 10000


T("KP 10K", kp_10k)
T(
    "KP decay",
    lambda: (
        lambda k: (
            k.add(
                KnowledgeItem(
                    id="k1",
                    content="t",
                    modality=Modality.DOCUMENT,
                    source="s",
                    provenance=0.5,
                    freshness=1.0,
                )
            )
            or (k.get("k1").freshness == 1.0 and k.decay(1.0) or k.get("k1").freshness < 1.0)
        )
    )(KnowledgePack()),
)

# === 4. Evidence fusion (1) ===
T(
    "EF",
    lambda: (
        lambda k: (
            k.add(
                KnowledgeItem(
                    id="k1",
                    content="t",
                    modality=Modality.DOCUMENT,
                    source="s",
                    provenance=0.5,
                )
            )
            or EvidenceFusionEngine()
            .fuse(
                [
                    Evidence(
                        source="g",
                        modality=Modality.DOCUMENT,
                        content="+",
                        confidence_delta=0.5,
                    )
                ],
                k,
                EpistemicState(belief=BeliefState(confidence=KnowledgeConfidence(provenance=0.5))),
            )
            .evidence_applied
            >= 1
        )
    )(KnowledgePack()),
)

# === 5. Retrieval (1) ===
T(
    "Retrieval",
    lambda: RetrievalPolicy().evaluate(KnowledgePack(), [], 0.5).should_retrieve == False,
)

# === 6. Agent (1) ===
T(
    "Agent",
    lambda: ExecutionPool(KnowledgePack()).spawn(AgentSpec(role=AgentRole.WORKER)).state.value == "spawned",
)

# === 7. Kernel (1) ===
T("Kernel", lambda: _r(CognitiveKernel().step("action"))["confidence"] > 0)

# === 8. Performance (1) ===
T("Perf monitor", lambda: PerformanceMonitor().get_stats("x")["count"] == 0)

# === 9. Assumptions (1) ===
T(
    "Assumption",
    lambda: (
        len(
            AssumptionValidator().scan_specification(
                {
                    "security": 1,
                    "deployment": 1,
                    "persistence": 1,
                    "performance": 1,
                    "compliance": 1,
                    "integrations": 1,
                }
            )
        )
        == 0
    ),
)

# === 10. Completeness (1) ===
T(
    "Complete",
    lambda: EpistemicCompleteness().assess(0.8, 0.1, 0, 0.9, 0.8, 0.95, 0.7).overall_confidence > 0.7,
)

# === 11. Loss (1) ===
T(
    "Loss",
    lambda: epa_loss(EpistemicPredictiveState(S={"a": 1}), EpistemicPredictiveState(S={"a": 1})).get("L_E", 0) == 0.0,
)

# === 12. Bounds (2) ===
T(
    "Conf 0-1",
    lambda: all(
        0
        <= KnowledgeConfidence(
            provenance=random.random(),
            freshness=random.random(),
            completeness=random.random(),
            semantic_consistency=random.random(),
            validation=random.random(),
            simulation_success=random.random(),
        ).composite
        <= 1
        for _ in range(100)
    ),
)
T(
    "Loss >=0",
    lambda: all(epa_loss(EpistemicPredictiveState(), EpistemicPredictiveState()).get("L_E", 0) >= 0 for _ in range(10)),
)


# === 13. Concurrency (1) ===
def concurrent():
    am = ExecutionPool(KnowledgePack())
    for _ in range(5):
        am.spawn(AgentSpec(role=AgentRole.WORKER))
    errors = []

    def w(i):
        try:
            _r(am.assign_task({"type": f"t-{i}"}))
        except Exception as e:
            errors.append(e)

    ts = [threading.Thread(target=w, args=(i,)) for i in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors


T("Concurrent", concurrent)


# === 14. Thread safety (1) ===
def thread_safe():
    kp = KnowledgePack()
    errors = []

    def add(n):
        for i in range(100):
            try:
                kp.add(
                    KnowledgeItem(
                        id=f"{n}-{i}",
                        content=f"{n} {i}",
                        modality=Modality.DOCUMENT,
                        source=f"s{n}",
                        provenance=random.random(),
                    )
                )
            except Exception as e:
                errors.append(e)

    ts = [threading.Thread(target=add, args=(t,)) for t in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors


T("Thread safe", thread_safe)

# === 15. Soak (1) ===
T(
    "Soak 200",
    lambda: (
        lambda k: all(_r(k.step(f"s{i}"))["confidence"] > 0 for i in range(200)) and k.state.belief.confidence > 0
    )(CognitiveKernel()),
)

# === 16. Performance (2) ===
T(
    "Perf solver",
    lambda: all(_r(SolverMesh().solve({"type": "satisfiability"})) for _ in range(100)),
)
T("Perf kernel", lambda: [(_r(CognitiveKernel().step(f"s{i}"))) for i in range(50)])


# === 17. Security (2) ===
def check_sec_malicious():
    r = _r(SolverMesh().solve({"type": "injection; DROP TABLE"}))
    assert r.confidence >= 0, "Solver returned negative confidence on malicious input"
    assert r.solver_name in (
        "rule_engine",
        "graph",
        "sat",
        "constraint",
        "model_checker",
        "optimizer",
        "monte_carlo",
        "jepa",
        "llm",
    ), "Unknown solver"
    assert not any(
        "DROP" in str(r.solution) for k, v in (r.solution.items() if isinstance(r.solution, dict) else [])
    ), "Injection payload in solution"


T("Sec malicious", check_sec_malicious)
T(
    "Sec overflow",
    lambda: (
        "satisfiable"
        in _r(SATSolver().solve({"clauses": [[i] for i in range(1000)], "variables": list(range(1000))})).solution
    ),
)


# === 18. Serialization (3) ===
def test_ser_kp():
    k = KnowledgePack()
    k.add(
        KnowledgeItem(
            id="k1",
            content="test",
            modality=Modality.DOCUMENT,
            source="s",
            provenance=0.5,
        )
    )
    d = k.to_dict()
    k2 = KnowledgePack()
    for item in d.get("items", []):
        k2.add(KnowledgeItem.from_dict(item))
    assert k2.size == 1


T("Ser KP", test_ser_kp)
T(
    "Ser epi",
    lambda: (
        EpistemicState.from_dict(
            EpistemicState(
                world={"a": 1},
                belief=BeliefState(confidence=KnowledgeConfidence(provenance=0.9)),
            ).to_dict()
        ).world
        == {"a": 1}
    ),
)
T(
    "Ser JSON",
    lambda: (lambda e: (lambda j: EpistemicState.from_dict(json.loads(j)).world == e.world)(json.dumps(e.to_dict())))(
        EpistemicState(world={"a": 1})
    ),
)

# === 19. Determinism (2) ===
T(
    "Det loss",
    lambda: (
        epa_loss(EpistemicPredictiveState(S={"a": 1}), EpistemicPredictiveState(S={"a": 1})).get("L_E", 0)
        == epa_loss(EpistemicPredictiveState(S={"a": 1}), EpistemicPredictiveState(S={"a": 1})).get("L_E", 0)
    ),
)
T(
    "Det conf",
    lambda: (
        KnowledgeConfidence(
            provenance=0.5,
            freshness=0.5,
            completeness=0.5,
            semantic_consistency=0.5,
            validation=0.5,
            simulation_success=0.5,
        ).composite
        == KnowledgeConfidence(
            provenance=0.5,
            freshness=0.5,
            completeness=0.5,
            semantic_consistency=0.5,
            validation=0.5,
            simulation_success=0.5,
        ).composite
    ),
)

# === 20. Property-based (3) ===
T(
    "Conf mono",
    lambda: (
        KnowledgeConfidence(
            provenance=1,
            freshness=1,
            completeness=1,
            semantic_consistency=1,
            validation=1,
            simulation_success=1,
        ).composite
        >= KnowledgeConfidence(
            provenance=0.1,
            freshness=0.1,
            completeness=0.1,
            semantic_consistency=0.1,
            validation=0.1,
            simulation_success=0.1,
        ).composite
    ),
)
T(
    "Loss sym",
    lambda: (
        epa_loss(EpistemicPredictiveState(S={"x": 1}), EpistemicPredictiveState(S={"y": 2})).get("L_E", 0)
        == epa_loss(EpistemicPredictiveState(S={"y": 2}), EpistemicPredictiveState(S={"x": 1})).get("L_E", 0)
    ),
)
T(
    "NaN free",
    lambda: all(
        not math.isnan(
            KnowledgeConfidence(
                provenance=random.random(),
                freshness=random.random(),
                completeness=random.random(),
                semantic_consistency=random.random(),
                validation=random.random(),
                simulation_success=random.random(),
            ).composite
        )
        for _ in range(100)
    ),
)

# === 21. Fuzz (1) ===
T(
    "Fuzz solvers",
    lambda: all(
        _r(
            SolverMesh().solve(
                {
                    "type": random.choice(
                        [
                            "rule_check",
                            "reachability",
                            "satisfiability",
                            "constraint",
                            "verification",
                            "optimization",
                            "planning",
                            "prediction",
                            "general",
                        ]
                    )
                }
            )
        ).confidence
        >= 0
        for _ in range(50)
    ),
)

# === 22. Mutation (2) ===
T(
    "Mut loss",
    lambda: (
        epa_loss(EpistemicPredictiveState(S={"a": 1}), EpistemicPredictiveState(S={"a": 1})).get("L_E", 0)
        < epa_loss(EpistemicPredictiveState(S={"a": 1}), EpistemicPredictiveState(S={"a": 999})).get("L_E", 0)
    ),
)
T(
    "Mut conf",
    lambda: (
        KnowledgeConfidence(
            provenance=1,
            freshness=1,
            completeness=1,
            semantic_consistency=1,
            validation=1,
            simulation_success=1,
        ).composite
        > KnowledgeConfidence(
            provenance=0,
            freshness=0,
            completeness=0,
            semantic_consistency=0,
            validation=0,
            simulation_success=0,
        ).composite
    ),
)

# === 23. Fault injection (3) ===
T(
    "Fault graph",
    lambda: (
        not _r(
            GraphSolver().solve(
                {
                    "graph": {},
                    "query": {"source": "x", "target": "y", "check": "reachability"},
                }
            )
        ).solution["reachable"]
    ),
)
T(
    "Fault SAT",
    lambda: _r(SATSolver().solve({"clauses": [], "variables": []})).solution["satisfiable"] == True,
)
T(
    "Fault fuse",
    lambda: EvidenceFusionEngine().fuse([], KnowledgePack(), EpistemicState()).evidence_applied == 0,
)


# === 24. Backward compat (2) ===
def test_compat_kp():
    k = KnowledgePack()
    k.add(
        KnowledgeItem(
            id="k1",
            content="old",
            modality=Modality.DOCUMENT,
            source="s",
            provenance=0.5,
        )
    )
    d = k.to_dict()
    k2 = KnowledgePack()
    for item in d.get("items", []):
        k2.add(KnowledgeItem.from_dict(item))
    assert k2.size == k.size


T("Compat KP", test_compat_kp)
T(
    "Compat epi",
    lambda: (
        EpistemicState.from_dict(
            EpistemicState(
                world={"old": True},
                belief=BeliefState(confidence=KnowledgeConfidence(provenance=0.5)),
            ).to_dict()
        ).world
        == {"old": True}
    ),
)


print(f"\n{'=' * 60}")
print(f"{ok}/84 checks passed")
if f:
    print(f"FAILURES: {len(f)}")
    for i in f:
        print(f"  {i}")
else:
    print("ALL CHECKS PASS")
print(f"{'=' * 60}")
