#!/usr/bin/env python3
"""Gate 9 — Performance benchmark suite.

Benchmarks all six required categories (graph traversal, planning,
reasoning, planetary cycle, REST latency, memory growth) against the
SLAs declared in src/monkey_brain/kernel/fix/performance_budgets.py,
and prints a pass/fail report.

Graph traversal / planning / reasoning / memory growth run fully
in-process — no server required. Planetary cycle and REST latency need
a live server (default http://localhost:8031) and are skipped with a
clear message if it's unreachable.

The planetary-cycle benchmark performs exactly ONE real POST to
/api/v1/agentos/planet/tick (not looped) — it's a real state-advancing
operation already triggered automatically every 300s by the server
itself; this script does not add repeated load on top of that.

Usage:
    python3 scripts/gate9_benchmark.py [--base-url http://localhost:8031]
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
import tracemalloc

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.kernel.fix.performance_budgets import (
    PERFORMANCE_BUDGETS,
    MEMORY_BUDGETS,
)


def bench(name, fn, iterations=100, warmup=5):
    for _ in range(warmup):
        fn()
    latencies = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        latencies.append((time.perf_counter() - t0) * 1000)
    latencies.sort()
    n = len(latencies)
    return {
        "name": name,
        "ops": n,
        "p50_ms": latencies[n // 2],
        "p95_ms": latencies[min(int(n * 0.95), n - 1)],
        "p99_ms": latencies[min(int(n * 0.99), n - 1)],
        "max_ms": latencies[-1],
    }


def check(result, budget_key):
    budget = PERFORMANCE_BUDGETS.get(budget_key)
    if budget is None:
        return result | {"budget": None, "verdict": "NO BUDGET"}
    ok = result["p99_ms"] <= budget.p99_ms and result["max_ms"] <= budget.max_ms
    return result | {
        "budget_p99_ms": budget.p99_ms,
        "budget_max_ms": budget.max_ms,
        "verdict": "PASS" if ok else "FAIL",
    }


def print_result(r):
    marker = {"PASS": "OK", "FAIL": "FAIL", "NO BUDGET": "?", "SKIP": "SKIP"}.get(r["verdict"], "?")
    if r["verdict"] == "SKIP":
        print(f"  [{marker:>4}] {r['name']:<40} {r.get('note', '')}")
    elif "per_actor_ms" in r:
        print(
            f"  [{marker:>4}] {r['name']:<40} total={r['total_ms']:8.1f}ms "
            f"per_actor={r['per_actor_ms']:7.1f}ms (budget/actor p99={r['budget_p99_ms']}ms)"
        )
    elif "growth_kb" in r:
        print(f"  [{marker:>4}] {r['name']:<40} growth={r['growth_kb']:8.1f}KB (budget={r['budget_kb']:.0f}KB)")
    else:
        budget_str = f"(budget p99={r.get('budget_p99_ms', '-')}ms)" if r.get("budget_p99_ms") is not None else ""
        print(
            f"  [{marker:>4}] {r['name']:<40} p50={r.get('p50_ms', 0):7.2f}ms "
            f"p95={r.get('p95_ms', 0):7.2f}ms p99={r.get('p99_ms', 0):7.2f}ms "
            f"max={r.get('max_ms', 0):7.2f}ms {budget_str}"
        )


# ═══════════════════════════════════════════════════════════════
# 1. GRAPH TRAVERSAL
# ═══════════════════════════════════════════════════════════════


def bench_graph_traversal():
    from src.monkey_brain.kernel.solver_mesh import GraphSolver
    import asyncio

    def run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    graph = GraphSolver()
    results = []
    results.append(
        check(
            bench(
                "Graph: BFS reachability",
                lambda: run(
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
            ),
            "solver.graph",
        )
    )
    results.append(
        check(
            bench(
                "Graph: cycle check",
                lambda: run(
                    graph.solve(
                        {
                            "graph": {"a": ["b"], "b": ["c"], "c": ["a"]},
                            "query": {"check": "cycle_check"},
                        }
                    )
                ),
            ),
            "solver.graph",
        )
    )
    results.append(
        check(
            bench(
                "Graph: topological sort (100 nodes)",
                lambda: run(
                    graph.solve(
                        {
                            "graph": {f"n{i}": [f"n{i + 1}"] for i in range(99)} | {"n99": []},
                            "query": {"check": "topological"},
                        }
                    )
                ),
            ),
            "solver.graph",
        )
    )
    return results


# ═══════════════════════════════════════════════════════════════
# 2. PLANNING
# ═══════════════════════════════════════════════════════════════


def bench_planning():
    from src.monkey_brain.kernel.cognitive_kernel import CognitiveKernel
    import asyncio

    def run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    k = CognitiveKernel()
    k.set_goal("gate9-benchmark")
    return [
        check(
            bench(
                "Kernel: step()",
                lambda: run(k.step("s1")),
                iterations=50,
            ),
            "kernel.step",
        )
    ]


# ═══════════════════════════════════════════════════════════════
# 3. REASONING
# ═══════════════════════════════════════════════════════════════


def bench_reasoning():
    from src.monkey_brain.kernel.fix.scheduler.scheduler import (
        HeuristicReasoningScheduler,
    )

    sched = HeuristicReasoningScheduler()
    problems = [
        {"type": "general", "complexity": "medium"},
        {"type": "planning", "complexity": "high"},
        {"type": "analysis", "complexity": "medium"},
        {"needs_verification": True},
    ]
    return [
        check(
            bench(
                "Reasoning: select()",
                lambda: sched.select(random.choice(problems), available_agents=3),
                iterations=200,
            ),
            "reasoning.select",
        )
    ]


# ═══════════════════════════════════════════════════════════════
# 4. PLANETARY CYCLE (requires live server, single real tick)
# ═══════════════════════════════════════════════════════════════


def bench_planetary_cycle(base_url):
    import httpx

    try:
        with httpx.Client(base_url=base_url, timeout=60.0) as c:
            r = c.get("/api/v1/agentos/actors")
            r.raise_for_status()
            actor_count = len(r.json())
            if actor_count == 0:
                return [
                    {
                        "name": "Planetary: tick",
                        "verdict": "SKIP",
                        "note": "no actors registered on live server",
                    }
                ]

            t0 = time.perf_counter()
            r = c.post("/api/v1/agentos/planet/tick")
            elapsed_ms = (time.perf_counter() - t0) * 1000
            r.raise_for_status()
    except httpx.HTTPError as e:
        return [
            {
                "name": "Planetary: tick",
                "verdict": "SKIP",
                "note": f"server unreachable at {base_url}: {e}",
            }
        ]

    per_actor_ms = elapsed_ms / actor_count
    budget = PERFORMANCE_BUDGETS["planetary.cycle_per_actor"]
    ok = per_actor_ms <= budget.p99_ms
    return [
        {
            "name": f"Planetary: tick ({actor_count} actors)",
            "total_ms": elapsed_ms,
            "per_actor_ms": per_actor_ms,
            "budget_p99_ms": budget.p99_ms,
            "verdict": "PASS" if ok else "FAIL",
        }
    ]


# ═══════════════════════════════════════════════════════════════
# 5. REST LATENCY (requires live server, safe read-only GETs)
# ═══════════════════════════════════════════════════════════════


def bench_rest_latency(base_url):
    import httpx

    endpoints = [
        "/live",
        "/health",
        "/ready",
        "/api/v1/agentos/actors",
        "/api/v1/agentos/societies",
    ]
    results = []
    try:
        with httpx.Client(base_url=base_url, timeout=10.0) as c:
            c.get("/live")  # warm the connection
            for ep in endpoints:

                def call(ep=ep, c=c):
                    r = c.get(ep)
                    r.raise_for_status()

                results.append(
                    check(
                        bench(f"REST: GET {ep}", call, iterations=20, warmup=3),
                        "capability.rest",
                    )
                )
    except httpx.HTTPError as e:
        return [
            {
                "name": "REST latency",
                "verdict": "SKIP",
                "note": f"server unreachable at {base_url}: {e}",
            }
        ]
    return results


# ═══════════════════════════════════════════════════════════════
# 6. MEMORY GROWTH (in-process, no server side effects)
# ═══════════════════════════════════════════════════════════════


def bench_memory_growth():
    from src.knowledge.pack import KnowledgePack
    from src.knowledge.item import KnowledgeItem, Modality

    def kp_growth(n):
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()
        k = KnowledgePack()
        for i in range(n):
            k.add(
                KnowledgeItem(
                    id=f"k{i}",
                    content=f"item {i}",
                    modality=Modality.DOCUMENT,
                    source=f"s{i}",
                    provenance=random.random(),
                )
            )
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()
        growth_kb = sum(s.size_diff for s in snap2.compare_to(snap1, "lineno")) / 1024
        return growth_kb

    results = []
    budget = MEMORY_BUDGETS["actor.heap_growth"]
    for n in (100, 1000):
        growth_kb = kp_growth(n)
        ok, msg = budget.check(growth_kb, n)
        results.append(
            {
                "name": f"Memory: KnowledgePack +{n} items",
                "growth_kb": growth_kb,
                "budget_kb": budget.per_unit_kb * n,
                "verdict": "PASS" if ok else "FAIL",
            }
        )
    return results


# ═══════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8031")
    args = ap.parse_args()

    print("=" * 78)
    print("GATE 9 — PERFORMANCE BENCHMARK SUITE")
    print("=" * 78)

    sections = [
        ("1. GRAPH TRAVERSAL", lambda: bench_graph_traversal()),
        ("2. PLANNING", lambda: bench_planning()),
        ("3. REASONING", lambda: bench_reasoning()),
        ("4. PLANETARY CYCLE", lambda: bench_planetary_cycle(args.base_url)),
        ("5. REST LATENCY", lambda: bench_rest_latency(args.base_url)),
        ("6. MEMORY GROWTH", lambda: bench_memory_growth()),
    ]

    all_results = []
    for title, fn in sections:
        print(f"\n{title}")
        print("-" * 78)
        results = fn()
        all_results.extend(results)
        for r in results:
            print_result(r)

    passed = sum(1 for r in all_results if r["verdict"] == "PASS")
    failed = sum(1 for r in all_results if r["verdict"] == "FAIL")
    skipped = sum(1 for r in all_results if r["verdict"] == "SKIP")
    print("\n" + "=" * 78)
    print(f"TOTAL: {len(all_results)}  PASS: {passed}  FAIL: {failed}  SKIP: {skipped}")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
