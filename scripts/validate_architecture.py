"""End-to-End Architecture Validation Suite.

Tests 7 scenarios through the complete cognitive loop:
PLAN → EXECUTE → SIMULATE → LEARN

Validates all architectural invariants for each scenario.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from typing import Any

BASE_URL = "http://localhost:8031/api/v1/agentos"

SCENARIOS = [
    {"name": "Todo Management", "query": "todo agent", "domain": "personal"},
    {
        "name": "Ecommerce – Place Order",
        "query": "place an ecommerce order",
        "domain": "ecommerce",
    },
    {
        "name": "Ecommerce – Order Management",
        "query": "manage ecommerce orders",
        "domain": "ecommerce",
    },
    {
        "name": "Ecommerce – Warehouse Management",
        "query": "manage ecommerce warehouse",
        "domain": "ecommerce",
    },
    {
        "name": "Ecommerce – Inventory Management",
        "query": "manage ecommerce inventory",
        "domain": "ecommerce",
    },
    {
        "name": "Ecommerce – Shipping Management",
        "query": "manage ecommerce shipping",
        "domain": "ecommerce",
    },
    {
        "name": "Robotics / AMR Fleet Management",
        "query": "manage autonomous mobile robots",
        "domain": "robotics",
    },
]


def api_call(endpoint: str, method: str = "GET", data: dict | None = None, timeout: int = 120) -> dict[str, Any]:
    url = f"{BASE_URL}/{endpoint}"
    payload = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def run_scenario(scenario: dict) -> dict[str, Any]:
    """Run a complete PLAN → EXECUTE → SIMULATE → LEARN pipeline."""
    result = {
        "scenario": scenario["name"],
        "domain": scenario["domain"],
        "query": scenario["query"],
        "planning": {"status": "FAIL", "details": {}},
        "execution": {"status": "FAIL", "details": {}},
        "simulation": {"status": "FAIL", "details": {}},
        "learning": {"status": "FAIL", "details": {}},
        "architecture": {"status": "FAIL", "details": {}},
    }

    # ── PLAN ──
    try:
        t0 = time.time()
        plan = api_call("plan", "POST", {"question": scenario["query"], "target": "execute"})
        plan_latency = (time.time() - t0) * 1000

        graph = plan.get("graph", {})
        graph_id = graph.get("graph_id", "")
        run_id = plan.get("run_id", "")
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        order = graph.get("execution_order", [])

        planning_ok = bool(graph_id) and bool(run_id) and len(nodes) > 0
        result["planning"]["status"] = "PASS" if planning_ok else "FAIL"
        result["planning"]["details"] = {
            "graph_id": graph_id,
            "run_id": run_id,
            "nodes": len(nodes),
            "edges": len(edges),
            "layers": len(order),
            "latency_ms": round(plan_latency, 2),
            "intent_ir": "present" if plan.get("intent_ir") else "missing",
        }

    except Exception as e:
        result["planning"]["status"] = "FAIL"
        result["planning"]["details"] = {"error": str(e)}
        return result

    # ── EXECUTE ──
    try:
        t0 = time.time()
        exec_resp = api_call("execute", "POST", plan)
        exec_latency = (time.time() - t0) * 1000

        exec_graph = exec_resp.get("graph", {})
        exec_graph_id = exec_graph.get("graph_id", "")
        exec_nodes = exec_graph.get("nodes", [])
        exec_edges = exec_graph.get("edges", [])
        exec_order = exec_graph.get("execution_order", [])

        exec_ok = exec_graph_id == graph_id and len(exec_nodes) == len(nodes) and len(exec_edges) == len(edges)
        result["execution"]["status"] = "PASS" if exec_ok else "FAIL"
        result["execution"]["details"] = {
            "graph_id_match": exec_graph_id == graph_id,
            "node_count_match": len(exec_nodes) == len(nodes),
            "edge_count_match": len(exec_edges) == len(edges),
            "llm_answered": exec_resp.get("llm_answered", False),
            "latency_ms": round(exec_latency, 2),
        }

    except Exception as e:
        result["execution"]["status"] = "FAIL"
        result["execution"]["details"] = {"error": str(e)}

    # ── SIMULATE ──
    try:
        t0 = time.time()
        sim_resp = api_call("simulate", "POST", plan)
        sim_latency = (time.time() - t0) * 1000

        sim_graph = sim_resp.get("graph", {})
        sim_graph_id = sim_graph.get("graph_id", "")
        sim_nodes = sim_graph.get("nodes", [])
        sim_edges = sim_graph.get("edges", [])
        sim_order = sim_graph.get("execution_order", [])

        sim_ok = sim_graph_id == graph_id and len(sim_nodes) == len(nodes) and len(sim_edges) == len(edges)
        result["simulation"]["status"] = "PASS" if sim_ok else "FAIL"
        result["simulation"]["details"] = {
            "graph_id_match": sim_graph_id == graph_id,
            "node_count_match": len(sim_nodes) == len(nodes),
            "edge_count_match": len(sim_edges) == len(edges),
            "grounding_score": sim_resp.get("grounding_score", 0),
            "latency_ms": round(sim_latency, 2),
        }

    except Exception as e:
        result["simulation"]["status"] = "FAIL"
        result["simulation"]["details"] = {"error": str(e)}

    # ── LEARN ──
    try:
        t0 = time.time()
        learn_resp = api_call("learn", "POST", {"batch_size": 2, "epochs": 2})
        learn_latency = (time.time() - t0) * 1000

        learn_ok = "final_perplexity" in learn_resp and "final_q_value" in learn_resp
        result["learning"]["status"] = "PASS" if learn_ok else "FAIL"
        result["learning"]["details"] = {
            "final_perplexity": learn_resp.get("final_perplexity", 0),
            "final_q_value": learn_resp.get("final_q_value", 0),
            "iterations": learn_resp.get("iterations_completed", 0),
            "latency_ms": round(learn_latency, 2),
        }

    except Exception as e:
        result["learning"]["status"] = "FAIL"
        result["learning"]["details"] = {"error": str(e)}

    # ── ARCHITECTURE INVARIANTS ──
    inv_graph_id = result["execution"]["details"].get("graph_id_match", False)
    inv_nodes = result["execution"]["details"].get("node_count_match", False)
    inv_edges = result["execution"]["details"].get("edge_count_match", False)
    inv_sim_graph = result["simulation"]["details"].get("graph_id_match", False)
    inv_sim_nodes = result["simulation"]["details"].get("node_count_match", False)
    inv_sim_edges = result["simulation"]["details"].get("edge_count_match", False)

    all_ok = inv_graph_id and inv_nodes and inv_edges and inv_sim_graph and inv_sim_nodes and inv_sim_edges
    result["architecture"]["status"] = "PASS" if all_ok else "FAIL"
    result["architecture"]["details"] = {
        "graph_id_preserved": inv_graph_id and inv_sim_graph,
        "node_count_preserved": inv_nodes and inv_sim_nodes,
        "edge_count_preserved": inv_edges and inv_sim_edges,
    }

    return result


def main():
    print("=" * 70)
    print("MonkeyBrain Cognitive OS — End-to-End Architecture Validation")
    print("=" * 70)
    print()

    all_results = []
    passed = 0
    failed = 0

    for i, scenario in enumerate(SCENARIOS, 1):
        print(f"[{i}/{len(SCENARIOS)}] {scenario['name']} ({scenario['query']})")
        print("-" * 50)

        result = run_scenario(scenario)
        all_results.append(result)

        statuses = {
            "Planning": result["planning"]["status"],
            "Execution": result["execution"]["status"],
            "Simulation": result["simulation"]["status"],
            "Learning": result["learning"]["status"],
            "Architecture": result["architecture"]["status"],
        }

        for stage, status in statuses.items():
            icon = "✓" if status == "PASS" else "✗"
            print(f"  {stage:15} {icon}")

        if all(s == "PASS" for s in statuses.values()):
            passed += 1
            print(f"  Result: PASS")
        else:
            failed += 1
            print(f"  Result: FAIL")

        details = result["planning"]["details"]
        if details:
            print(
                f"  Graph: {details.get('graph_id', '?')[:12]}... nodes={details.get('nodes', 0)} edges={details.get('edges', 0)}"
            )
        print()

    print("=" * 70)
    print(f"VALIDATION SUMMARY")
    print("=" * 70)
    print(f"  Total scenarios: {len(SCENARIOS)}")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    print()

    # Architecture invariants
    all_graph_ids = set()
    for r in all_results:
        g = r["planning"]["details"].get("graph_id", "")
        if g:
            all_graph_ids.add(g)

    print(f"  Unique graph IDs across scenarios: {len(all_graph_ids)}")
    print(f"  All scenarios use unique graphs: {'✓' if len(all_graph_ids) == len(all_results) else '✗'}")
    print()

    # Invariant summary
    inv_violations = 0
    for r in all_results:
        arch = r["architecture"]["details"]
        if not arch.get("graph_id_preserved", True):
            inv_violations += 1
            print(f"  ✗ {r['scenario']}: graph_id NOT preserved")
        if not arch.get("node_count_preserved", True):
            inv_violations += 1
            print(f"  ✗ {r['scenario']}: node count NOT preserved")
        if not arch.get("edge_count_preserved", True):
            inv_violations += 1
            print(f"  ✗ {r['scenario']}: edge count NOT preserved")

    if inv_violations == 0:
        print(f"  All architecture invariants satisfied: ✓")
    else:
        print(f"  Architecture invariant violations: {inv_violations}")

    # Metrics summary
    print()
    print(f"  Metrics:")
    for r in all_results:
        p = r["planning"]["details"]
        e = r["execution"]["details"]
        l = r["learning"]["details"]
        print(
            f"    {r['scenario'][:30]:30} plan={p.get('latency_ms', 0):>8.0f}ms  exec={e.get('latency_ms', 0):>8.0f}ms  Q={l.get('final_q_value', 0):.3f}  ppl={l.get('final_perplexity', 0):.3f}"
        )

    print()
    if failed == 0:
        print("  Recommendation: Ready for Release Candidate")
    elif failed <= 2:
        print("  Recommendation: Ready after Minor Fixes")
    else:
        print("  Recommendation: Architecture Incomplete")

    # Write results to file
    with open("/tmp/validation_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Full results saved to /tmp/validation_results.json")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
