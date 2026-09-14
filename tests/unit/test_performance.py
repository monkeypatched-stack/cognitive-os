"""Performance Benchmark Suite — Sprint D.

Measures latency, memory, throughput, stress, and convergence
for all critical paths. Produces actual metrics, not pass/fail.
"""

import asyncio
import os
import random
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Any

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

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
from src.monkey_brain.kernel.loss_driven_repair import LossDrivenRepair


def _r(c):
    l = asyncio.new_event_loop()
    try:
        return l.run_until_complete(c)
    finally:
        l.close()


@dataclass
class BenchResult:
    name: str = ""
    ops: int = 0
    total_ms: float = 0.0
    avg_ms: float = 0.0
    min_ms: float = float("inf")
    max_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    throughput: float = 0.0  # ops/sec
    memory_kb: float = 0.0

    def __str__(self):
        return (
            f"  {self.name:<40} {self.avg_ms:>7.2f}ms  "
            f"[{self.min_ms:.1f}-{self.max_ms:.1f}]  "
            f"p50={self.p50_ms:.1f}  p95={self.p95_ms:.1f}  "
            f"p99={self.p99_ms:.1f}  "
            f"{self.throughput:>10.0f} ops/s  "
            f"{self.memory_kb:>7.1f} KB"
        )


def bench(name, fn, iterations=100, warmup=5):
    """Run a benchmark with warmup and collect latency percentiles."""
    for _ in range(warmup):
        fn()

    latencies = []
    t0 = time.perf_counter()
    for _ in range(iterations):
        t1 = time.perf_counter()
        fn()
        latencies.append((time.perf_counter() - t1) * 1000)
    total = (time.perf_counter() - t0) * 1000

    latencies.sort()
    n = len(latencies)
    return BenchResult(
        name=name,
        ops=n,
        total_ms=total,
        avg_ms=total / n,
        min_ms=latencies[0],
        max_ms=latencies[-1],
        p50_ms=latencies[n // 2],
        p95_ms=latencies[int(n * 0.95)],
        p99_ms=latencies[int(n * 0.99)],
        throughput=n / (total / 1000),
    )


def bench_memory(name, fn):
    """Measure memory delta of an operation."""
    tracemalloc.start()
    s1 = tracemalloc.take_snapshot()
    fn()
    s2 = tracemalloc.take_snapshot()
    delta = sum(s.size_diff for s in s2.compare_to(s1, "lineno"))
    tracemalloc.stop()
    return delta / 1024  # KB


# ═══════════════════════════════════════════════════════════════
# 1. LATENCY BENCHMARKS
# ═══════════════════════════════════════════════════════════════


def run_latency_benchmarks():
    results = []
    graph = GraphSolver()
    sat = SATSolver()
    constraint = ConstraintSolver()
    mc = ModelCheckerSolver()
    optim = OptimizerSolver()
    mcts = MonteCarloSolver()
    jepa = JEPAWorldModel()

    # Solver latencies (100 iterations each)
    results.append(
        bench(
            "Solver: Graph BFS",
            lambda: _r(
                graph.solve(
                    {
                        "graph": {"a": ["b"], "b": ["c"], "c": []},
                        "query": {
                            "source": "a",
                            "target": "c",
                            "check": "reachability",
                        },
                    }
                )
            ),
        )
    )
    results.append(
        bench(
            "Solver: Graph Cycle",
            lambda: _r(
                graph.solve(
                    {
                        "graph": {"a": ["b"], "b": ["c"], "c": ["a"]},
                        "query": {"check": "cycle_check"},
                    }
                )
            ),
        )
    )
    results.append(
        bench(
            "Solver: Graph Topo (100 nodes)",
            lambda: _r(
                graph.solve(
                    {
                        "graph": {f"n{i}": [f"n{i + 1}"] for i in range(99)} | {"n99": []},
                        "query": {"check": "topological"},
                    }
                )
            ),
        )
    )
    results.append(
        bench(
            "Solver: SAT 19",
            lambda: _r(
                sat.solve(
                    {
                        "clauses": [[i, -(i + 1)] for i in range(1, 20)],
                        "variables": list(range(1, 20)),
                    }
                )
            ),
        )
    )
    results.append(
        bench(
            "Solver: SAT 100",
            lambda: _r(
                sat.solve(
                    {
                        "clauses": [[i, -(i + 1)] for i in range(1, 100)],
                        "variables": list(range(1, 100)),
                    }
                )
            ),
        )
    )
    results.append(
        bench(
            "Solver: Constraint 3-var",
            lambda: _r(
                constraint.solve(
                    {
                        "variables": {
                            "x": list(range(10)),
                            "y": list(range(10)),
                            "z": list(range(10)),
                        },
                        "constraints": [{"type": "all_different", "variables": ["x", "y", "z"]}],
                    }
                )
            ),
        )
    )
    results.append(
        bench(
            "Solver: ModelChecker",
            lambda: _r(
                mc.solve(
                    {
                        "model": {"x": 5, "y": 10},
                        "invariants": [
                            {"type": "positive", "variable": "x"},
                            {"type": "range", "variable": "y", "min": 0, "max": 20},
                        ],
                    }
                )
            ),
        )
    )
    results.append(
        bench(
            "Solver: Optimizer",
            lambda: _r(
                optim.solve(
                    {
                        "objective": "minimize",
                        "variables": {"x": 5.0, "y": 3.0},
                        "iterations": 200,
                    }
                )
            ),
        )
    )
    results.append(
        bench(
            "Solver: MCTS 50 sim",
            lambda: _r(mcts.solve({"actions": ["a", "b", "c"], "n_simulations": 50})),
        )
    )
    results.append(
        bench(
            "Solver: MCTS 200 sim",
            lambda: _r(mcts.solve({"actions": ["a", "b", "c", "d"], "n_simulations": 200})),
        )
    )
    results.append(
        bench(
            "Solver: JEPA 10-pt",
            lambda: _r(
                jepa.solve(
                    {
                        "current_state": {"x": 1},
                        "action": "move",
                        "history": [{"x": i} for i in range(10)],
                    }
                )
            ),
        )
    )
    results.append(
        bench(
            "Solver: JEPA 50-pt",
            lambda: _r(
                jepa.solve(
                    {
                        "current_state": {"x": 1},
                        "action": "move",
                        "history": [{"x": i * 0.1} for i in range(50)],
                    }
                )
            ),
        )
    )
    results.append(bench("Solver: LLM", lambda: _r(LLMSolver().solve({}))))
    results.append(
        bench(
            "SolverMesh: full dispatch",
            lambda: _r(SolverMesh().solve({"type": "satisfiability", "clauses": [[1, 2]], "variables": [1, 2]})),
        )
    )

    # Knowledge latencies
    def kp_add_100():
        k = KnowledgePack()
        for i in range(100):
            k.add(
                KnowledgeItem(
                    id=f"k{i}",
                    content=f"item {i}",
                    modality=Modality.DOCUMENT,
                    source=f"s{i}",
                    provenance=random.random(),
                )
            )

    results.append(bench("Knowledge: add 100", kp_add_100, iterations=100))

    def kp_add_1000():
        k = KnowledgePack()
        for i in range(1000):
            k.add(
                KnowledgeItem(
                    id=f"k{i}",
                    content=f"item {i}",
                    modality=Modality.DOCUMENT,
                    source=f"s{i}",
                    provenance=random.random(),
                )
            )

    results.append(bench("Knowledge: add 1K", kp_add_1000, iterations=20))

    def kp_fuse_100():
        k = KnowledgePack()
        for i in range(100):
            k.add(
                KnowledgeItem(
                    id=f"k{i}",
                    content=f"item {i}",
                    modality=Modality.DOCUMENT,
                    source=f"s{i}",
                    provenance=random.random(),
                )
            )
        k.fuse()

    results.append(bench("Knowledge: fuse 100", kp_fuse_100, iterations=50))

    def kp_fuse_1000():
        k = KnowledgePack()
        for i in range(1000):
            k.add(
                KnowledgeItem(
                    id=f"k{i}",
                    content=f"item {i}",
                    modality=Modality.DOCUMENT,
                    source=f"s{i}",
                    provenance=random.random(),
                )
            )
        k.fuse()

    results.append(bench("Knowledge: fuse 1K", kp_fuse_1000, iterations=20))

    def kp_decay_100():
        k = KnowledgePack()
        for i in range(100):
            k.add(
                KnowledgeItem(
                    id=f"k{i}",
                    content=f"item {i}",
                    modality=Modality.DOCUMENT,
                    source=f"s{i}",
                    provenance=random.random(),
                )
            )
        k.decay(steps=1)

    results.append(bench("Knowledge: decay 100", kp_decay_100, iterations=50))

    # Kernel latencies
    k = CognitiveKernel()
    k.set_goal("benchmark")
    results.append(bench("Kernel: 1 step", lambda: _r(k.step("s1")), iterations=50))
    results.append(
        bench(
            "Kernel: 10 steps",
            lambda: [_r(k.step(f"s{i}")) for i in range(10)],
            iterations=20,
        )
    )
    results.append(
        bench(
            "Kernel: 100 steps",
            lambda: [_r(k.step(f"s{i}")) for i in range(100)],
            iterations=5,
        )
    )

    # Agent latencies
    ep = ExecutionPool(KnowledgePack())
    for _ in range(10):
        ep.spawn(AgentSpec(role=AgentRole.WORKER))
    results.append(
        bench(
            "Agent: 20 tasks",
            lambda: [_r(ep.assign_task({"type": f"t-{i}"})) for i in range(20)],
            iterations=20,
        )
    )

    return results


# ═══════════════════════════════════════════════════════════════
# 2. MEMORY BENCHMARKS
# ═══════════════════════════════════════════════════════════════


def run_memory_benchmarks():
    results = []

    # Knowledge pack scaling
    def kp_100():
        k = KnowledgePack()
        for i in range(100):
            k.add(
                KnowledgeItem(
                    id=f"k{i}",
                    content=f"item {i}",
                    modality=Modality.DOCUMENT,
                    source=f"s{i}",
                    provenance=random.random(),
                )
            )

    results.append(("Knowledge: 100 items", bench_memory("100 items", kp_100)))

    def kp_1000():
        k = KnowledgePack()
        for i in range(1000):
            k.add(
                KnowledgeItem(
                    id=f"k{i}",
                    content=f"item {i}",
                    modality=Modality.DOCUMENT,
                    source=f"s{i}",
                    provenance=random.random(),
                )
            )

    results.append(("Knowledge: 1000 items", bench_memory("1000 items", kp_1000)))

    def kp_10000():
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

    results.append(("Knowledge: 10000 items", bench_memory("10000 items", kp_10000)))

    # Kernel state
    results.append(("Kernel: single instance", bench_memory("kernel", lambda: CognitiveKernel())))

    # Agent pool
    def ep_100():
        ep = ExecutionPool(KnowledgePack())
        for _ in range(100):
            ep.spawn(AgentSpec(role=AgentRole.WORKER))

    results.append(("Agent pool: 100 agents", bench_memory("100 agents", ep_100)))

    return results


# ═══════════════════════════════════════════════════════════════
# 3. THROUGHPUT BENCHMARKS
# ═══════════════════════════════════════════════════════════════


def run_throughput_benchmarks():
    results = []
    sm = SolverMesh()

    # Solver dispatch throughput
    def dispatch_100():
        for i in range(100):
            _r(
                sm.solve(
                    {
                        "type": random.choice(
                            [
                                "reachability",
                                "satisfiability",
                                "constraint",
                                "verification",
                            ]
                        ),
                        "graph": {"a": ["b"]},
                        "query": {
                            "source": "a",
                            "target": "b",
                            "check": "reachability",
                        },
                        "clauses": [[1, 2]],
                        "variables": [1, 2],
                        "variables": {"x": [1]},
                        "constraints": [],
                        "model": {"x": 1},
                        "invariants": [],
                    }
                )
            )

    results.append(bench("Throughput: 100 dispatches", dispatch_100, iterations=10))

    # Knowledge throughput
    def kp_insert_1000():
        k = KnowledgePack()
        for i in range(1000):
            k.add(
                KnowledgeItem(
                    id=f"k{i}",
                    content=f"item {i}",
                    modality=Modality.DOCUMENT,
                    source=f"s{i}",
                    provenance=random.random(),
                )
            )

    results.append(bench("Throughput: 1K insert", kp_insert_1000, iterations=10))

    # Agent task throughput
    ep = ExecutionPool(KnowledgePack())
    for _ in range(10):
        ep.spawn(AgentSpec(role=AgentRole.WORKER))

    def agent_50():
        for i in range(50):
            _r(ep.assign_task({"type": f"t-{i}"}))

    results.append(bench("Throughput: 50 agent tasks", agent_50, iterations=10))

    # Kernel throughput
    def kernel_50():
        k = CognitiveKernel()
        k.set_goal("throughput")
        for i in range(50):
            _r(k.step(f"s{i}"))

    results.append(bench("Throughput: 50 kernel steps", kernel_50, iterations=5))

    return results


# ═══════════════════════════════════════════════════════════════
# 4. STRESS BENCHMARKS
# ═══════════════════════════════════════════════════════════════


def run_stress_benchmarks():
    results = []

    # SAT: 500 variables (DPLL recursion depth ~500 is safe at 1000 limit)
    results.append(
        bench(
            "Stress: SAT 200 vars",
            lambda: _r(
                SATSolver().solve(
                    {
                        "clauses": [[i, -(i + 1)] for i in range(1, 200)],
                        "variables": list(range(1, 200)),
                    }
                )
            ),
            iterations=5,
            warmup=1,
        )
    )

    # SAT: 500 variables (pushes limit, may need sys.setrecursionlimit)
    import sys as _sys

    old_limit = _sys.getrecursionlimit()
    _sys.setrecursionlimit(2000)
    results.append(
        bench(
            "Stress: SAT 500 vars",
            lambda: _r(
                SATSolver().solve(
                    {
                        "clauses": [[i, -(i + 1)] for i in range(1, 500)],
                        "variables": list(range(1, 500)),
                    }
                )
            ),
            iterations=3,
            warmup=1,
        )
    )
    _sys.setrecursionlimit(old_limit)

    # Graph: 500 nodes (DFS recursion limit safe at 1000)
    results.append(
        bench(
            "Stress: Graph 500 nodes",
            lambda: _r(
                GraphSolver().solve(
                    {
                        "graph": {f"n{i}": [f"n{i + 1}"] for i in range(499)} | {"n499": []},
                        "query": {"check": "topological"},
                    }
                )
            ),
            iterations=10,
            warmup=1,
        )
    )

    # Graph: 500 nodes with cycle
    results.append(
        bench(
            "Stress: Graph 500 cycle",
            lambda: _r(
                GraphSolver().solve(
                    {
                        "graph": {f"n{i}": [f"n{i + 1}"] for i in range(499)} | {"n499": ["n0"]},
                        "query": {"check": "cycle_check"},
                    }
                )
            ),
            iterations=10,
            warmup=1,
        )
    )

    # Knowledge: 20K items
    def kp_20k():
        k = KnowledgePack()
        for i in range(20000):
            k.add(
                KnowledgeItem(
                    id=f"k{i}",
                    content=f"item {i}",
                    modality=Modality.DOCUMENT,
                    source=f"s{i}",
                    provenance=random.random(),
                )
            )
        k.fuse()

    results.append(bench("Stress: KP 20K items", kp_20k, iterations=3, warmup=1))

    # Kernel: 300 steps
    def kernel_300():
        k = CognitiveKernel()
        k.set_goal("stress")
        for i in range(300):
            _r(k.step(f"s{i}"))

    results.append(bench("Stress: Kernel 300 steps", kernel_300, iterations=2, warmup=1))

    # Agent: 200 tasks
    def agent_200():
        ep = ExecutionPool(KnowledgePack())
        for _ in range(20):
            ep.spawn(AgentSpec(role=AgentRole.WORKER))
        for i in range(200):
            _r(ep.assign_task({"type": f"t-{i}"}))

    results.append(bench("Stress: 200 agent tasks", agent_200, iterations=5, warmup=1))

    # MCTS: 1000 simulations
    results.append(
        bench(
            "Stress: MCTS 1000 sim",
            lambda: _r(MonteCarloSolver().solve({"actions": ["a", "b", "c", "d", "e"], "n_simulations": 1000})),
            iterations=5,
            warmup=1,
        )
    )

    # Optimizer: 1000 iterations
    results.append(
        bench(
            "Stress: Optimizer 1000 iter",
            lambda: _r(
                OptimizerSolver().solve(
                    {
                        "objective": "minimize",
                        "variables": {"x": 5.0},
                        "iterations": 1000,
                    }
                )
            ),
            iterations=5,
            warmup=1,
        )
    )

    return results


# ═══════════════════════════════════════════════════════════════
# 5. CONVERGENCE BENCHMARKS
# ═══════════════════════════════════════════════════════════════


def run_convergence_benchmarks():
    results = []

    def converge_easy():
        repair = LossDrivenRepair(loss_threshold=0.05, max_iterations=10)
        return repair.run(
            0.3,
            {
                "test_results": {},
                "ddd_results": {},
                "govern_results": {},
                "runtime_errors": [],
            },
            {"loss": 0.3},
        )

    r = converge_easy()
    iters = len(r.iterations) if hasattr(r, "iterations") and isinstance(r.iterations, list) else 0
    final = r.iterations[-1].loss_after if iters > 0 else r.initial_loss
    results.append(("Repair: easy (loss=0.3)", r.initial_loss, final, iters))

    def converge_medium():
        repair = LossDrivenRepair(loss_threshold=0.05, max_iterations=20)
        return repair.run(
            0.6,
            {
                "test_results": {},
                "ddd_results": {},
                "govern_results": {},
                "runtime_errors": [],
            },
            {"loss": 0.6},
        )

    r = converge_medium()
    iters = len(r.iterations) if hasattr(r, "iterations") and isinstance(r.iterations, list) else 0
    final = r.iterations[-1].loss_after if iters > 0 else r.initial_loss
    results.append(("Repair: medium (loss=0.6)", r.initial_loss, final, iters))

    def converge_hard():
        repair = LossDrivenRepair(loss_threshold=0.05, max_iterations=30)
        return repair.run(
            0.95,
            {
                "test_results": {},
                "ddd_results": {},
                "govern_results": {},
                "runtime_errors": [],
            },
            {"loss": 0.95},
        )

    r = converge_hard()
    iters = len(r.iterations) if hasattr(r, "iterations") and isinstance(r.iterations, list) else 0
    final = r.iterations[-1].loss_after if iters > 0 else r.initial_loss
    results.append(("Repair: hard (loss=0.95)", r.initial_loss, final, iters))

    # Kernel step convergence
    k = CognitiveKernel()
    k.set_goal("converge")
    confidences = []
    for i in range(50):
        step = _r(k.step(f"s{i}"))
        confidences.append(step.get("confidence", 0))
    final_10 = confidences[-10:]
    avg_final = sum(final_10) / len(final_10)
    variance_final = sum((x - avg_final) ** 2 for x in final_10) / len(final_10)
    results.append(
        (
            "Kernel: confidence convergence",
            confidences[0],
            confidences[-1],
            50,
            avg_final,
            variance_final,
        )
    )

    # Knowledge fusion stability
    k = KnowledgePack()
    for i in range(500):
        k.add(
            KnowledgeItem(
                id=f"k{i}",
                content=f"item {i}",
                modality=Modality.DOCUMENT,
                source=f"s{i}",
                provenance=random.random(),
            )
        )
    fusions = []
    for _ in range(10):
        f = k.fuse()
        fusions.append(f.fused_confidence)
    avg_fusion = sum(fusions) / len(fusions)
    var_fusion = sum((x - avg_fusion) ** 2 for x in fusions) / len(fusions)
    results.append(("KP: fusion stability", fusions[0], fusions[-1], 10, avg_fusion, var_fusion))

    return results


# ═══════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 72)
    print("SPRINT D — PERFORMANCE BENCHMARK SUITE")
    print("=" * 72)

    # 1. Latency
    print("\n1. LATENCY (100 iterations, 5 warmup)")
    print("-" * 72)
    print(f"  {'Operation':<40} {'Avg':>7}  {'Range':>20}  {'Percentiles':>25}  {'Throughput':>12}  {'Memory':>8}")
    print("-" * 72)
    for r in run_latency_benchmarks():
        print(r)

    # 2. Memory
    print("\n2. MEMORY")
    print("-" * 72)
    for name, kb in run_memory_benchmarks():
        print(f"  {name:<40} {kb:>8.1f} KB")

    # 3. Throughput
    print("\n3. THROUGHPUT")
    print("-" * 72)
    for r in run_throughput_benchmarks():
        print(f"  {r.name:<40} {r.throughput:>10.0f} ops/s  ({r.avg_ms:.2f}ms avg)")

    # 4. Stress
    print("\n4. STRESS (extreme inputs)")
    print("-" * 72)
    for r in run_stress_benchmarks():
        print(f"  {r.name:<40} {r.avg_ms:>8.1f}ms  {r.throughput:>10.0f} ops/s")

    # 5. Convergence
    print("\n5. CONVERGENCE")
    print("-" * 72)
    for item in run_convergence_benchmarks():
        name = item[0]
        if "Repair" in name:
            _, initial, final, iters = item
            print(f"  {name:<40} {initial:.3f} → {final:.3f}  ({iters} iterations)")
        elif "confidence" in name.lower():
            _, first, last, steps, avg, var = item
            print(f"  {name:<40} {first:.3f} → {last:.3f}  (avg={avg:.3f}, var={var:.6f})")
        elif "fusion" in name.lower():
            _, first, last, iters, avg, var = item
            print(f"  {name:<40} {first:.3f} → {last:.3f}  (avg={avg:.3f}, var={var:.6f})")

    print("\n" + "=" * 72)
    print("BENCHMARK COMPLETE")
    print("=" * 72)
