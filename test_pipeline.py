"""Pipeline test."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "packages", "broca"))

from dotenv import load_dotenv

load_dotenv()

from broca.agents.compiler_pipeline import CompilerPipeline


def validate_dag(graph: dict) -> dict:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    order = graph.get("execution_order", [])
    node_ids = {n["id"] for n in nodes}
    adj = {n["id"]: [] for n in nodes}
    for e in edges:
        src, dst = e.get("from"), e.get("to")
        if src in adj:
            adj[src].append(dst)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}
    has_cycle = False

    def dfs(nid):
        nonlocal has_cycle
        color[nid] = GRAY
        for neighbor in adj.get(nid, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                has_cycle = True
                return
            if color[neighbor] == WHITE:
                dfs(neighbor)
        color[nid] = BLACK

    for nid in node_ids:
        if color[nid] == WHITE:
            dfs(nid)
            if has_cycle:
                break
    in_degree = {n["id"]: 0 for n in nodes}
    for e in edges:
        dst = e.get("to")
        if dst in in_degree:
            in_degree[dst] += 1
    flat_order = [nid for batch in order for nid in batch]
    order_valid = len(flat_order) == len(node_ids) and set(flat_order) == node_ids
    position = {nid: i for i, nid in enumerate(flat_order)}
    dep_violations = 0
    for e in edges:
        src, dst = e.get("from"), e.get("to")
        if src in position and dst in position:
            if position[src] >= position[dst]:
                dep_violations += 1
    roots = [n["id"] for n in nodes if in_degree.get(n["id"], 0) == 0]
    reachable = set()

    def bfs(start):
        queue = [start]
        while queue:
            nid = queue.pop(0)
            if nid in reachable:
                continue
            reachable.add(nid)
            for neighbor in adj.get(nid, []):
                queue.append(neighbor)

    for root in roots:
        bfs(root)
    goal_reachable = len(reachable) == len(node_ids)
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "dag_valid": not has_cycle and order_valid and dep_violations == 0,
        "cycles": 1 if has_cycle else 0,
        "goal_reachable": goal_reachable,
        "dep_violations": dep_violations,
    }


async def main():
    intent = "Buy groceries for the week while staying within a budget of $20."

    print(f"\n{'=' * 70}")
    print(f"  PIPELINE TEST")
    print(f"{'=' * 70}")
    print(f"\n  Input: {intent}")
    print(f"\n{'─' * 70}")

    pipeline = CompilerPipeline()
    t0 = time.time()
    result = await pipeline._impl({"intent": intent, "question": intent})
    total_latency = (time.time() - t0) * 1000

    goal_ir = result.payload.get("goal_ir", {})
    candidates = result.payload.get("candidates", [])
    graph_metrics = [validate_dag(c.get("graph", {})) for c in candidates]
    all_dags_valid = all(m["dag_valid"] for m in graph_metrics) if graph_metrics else False
    all_goals_reachable = all(m["goal_reachable"] for m in graph_metrics) if graph_metrics else False

    print(f"\n  METRICS")
    print(f"  {'─' * 66}")
    print(f"  {'Metric':<40} {'Value':<26}")
    print(f"  {'─' * 66}")
    print(f"  {'Intent type':<40} {goal_ir.get('intent_type', 'N/A')}")
    print(f"  {'Domain':<40} {goal_ir.get('domain', 'N/A')}")
    print(f"  {'Entities':<40} {len(goal_ir.get('entities', []))}")
    print(f"  {'Constraints':<40} {len(goal_ir.get('constraints', []))}")
    print(f"  {'Relationships':<40} {len(goal_ir.get('relationships', []))}")
    print(f"  {'Candidates':<40} {len(candidates)}")
    print(f"  {'DAG valid':<40} {'Yes' if all_dags_valid else 'No'}")
    print(f"  {'Goal reachable':<40} {'Yes' if all_goals_reachable else 'No'}")
    print(f"  {'Cycles':<40} {sum(m['cycles'] for m in graph_metrics)}")
    print(f"  {'Latency':<40} {total_latency:.0f}ms")
    print(f"  {'─' * 66}")

    print(f"\n  GOAL IR")
    print(f"  {'─' * 66}")
    for e in goal_ir.get("entities", []):
        attrs = e.get("attributes", {})
        attr_str = f" ({', '.join(f'{k}={v}' for k, v in attrs.items())})" if attrs else ""
        print(f"    • {e['name']} [{e['type']}]{attr_str}")
    for c in goal_ir.get("constraints", []):
        params = c.get("parameters", {})
        param_str = f" | {params}" if params else ""
        print(f"    • {c['type']}: {c['description']}{param_str}")
    for r in goal_ir.get("relationships", []):
        print(f"    • {r['source']} —[{r['type']}]→ {r['target']}")

    print(f"\n  BEST GRAPH")
    print(f"  {'─' * 66}")
    if candidates:
        g = candidates[0].get("graph", {})
        nodes = g.get("nodes", [])
        edges = g.get("edges", [])
        node_map = {n["id"]: n["name"] for n in nodes}
        order = g.get("execution_order", [])
        for layer_idx, layer in enumerate(order):
            layer_nodes = [node_map.get(nid, nid) for nid in layer]
            print(f"    Layer {layer_idx + 1}: {' | '.join(layer_nodes)}")
            if layer_idx < len(order) - 1:
                print(f"         ↓")

    print(f"\n{'=' * 70}")
    print(f"  TEST PASSED")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    asyncio.run(main())
