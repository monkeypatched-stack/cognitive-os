"""Graph."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.monkey_brain.kernel.predict.graph_solver.base import (
    ISolver,
    SolverClass,
    SolverResult,
)

if TYPE_CHECKING:
    from src.monkey_brain.kernel.execute.graph import ExecutionGraph


class GraphSolver(ISolver):
    """Graph-based solver for reachability, dependencies, cycles.

    Uses BFS/DFS for reachability and Tarjan's for cycle detection.
    """

    name = "graph"
    solver_class = SolverClass.GRAPH

    def can_solve(self, problem: dict) -> float:
        return 0.9 if problem.get("type") in ("reachability", "dependency", "cycle_check") else 0.1

    async def solve(self, problem: dict) -> SolverResult:
        graph = problem.get("graph", {})
        query = problem.get("query", {})
        source = query.get("source", "")
        target = query.get("target", "")
        check_type = query.get("check", "reachability")

        if check_type == "cycle_check":
            has_cycle, cycle = self._find_cycle(graph)
            return SolverResult(
                solver_name=self.name,
                solver_class=self.solver_class,
                solution={"has_cycle": has_cycle, "cycle": cycle},
                confidence=0.95,
                proof=f"Cycle detection: {'found' if has_cycle else 'none'}",
            )

        if check_type == "reachability" and source and target:
            path = self._bfs(graph, source, target)
            reachable = path is not None
            return SolverResult(
                solver_name=self.name,
                solver_class=self.solver_class,
                solution={"reachable": reachable, "path": path or []},
                confidence=0.95,
                proof=f"BFS from {source} to {target}: {'reachable' if reachable else 'unreachable'}",
            )

        topo = self._topological_sort(graph)
        return SolverResult(
            solver_name=self.name,
            solver_class=self.solver_class,
            solution={"topological_order": topo, "is_dag": topo is not None},
            confidence=0.9,
            proof=f"Topological sort: {'valid' if topo else 'has cycle'}",
        )

    def _bfs(self, graph: dict, source: str, target: str) -> list[str] | None:
        from collections import deque

        visited = set()
        queue = deque([(source, [source])])
        while queue:
            node, path = queue.popleft()
            if node == target:
                return path
            if node in visited:
                continue
            visited.add(node)
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    queue.append((neighbor, path + [neighbor]))
        return None

    def _find_cycle(self, graph: dict) -> tuple[bool, list[str]]:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {node: WHITE for node in graph}
        parent = {}

        for node in graph:
            if color[node] == WHITE:
                cycle = self._dfs_cycle(graph, node, color, parent, WHITE, GRAY, BLACK)
                if cycle:
                    return True, cycle
        return False, []

    def _dfs_cycle(self, graph, node, color, parent, WHITE, GRAY, BLACK):
        color[node] = GRAY
        for neighbor in graph.get(node, []):
            if neighbor not in color:
                color[neighbor] = WHITE
            if color[neighbor] == GRAY:
                cycle = [neighbor, node]
                current = node
                while current != neighbor:
                    current = parent.get(current)
                    if current is None:
                        break
                    cycle.append(current)
                return list(reversed(cycle))
            if color[neighbor] == WHITE:
                parent[neighbor] = node
                result = self._dfs_cycle(graph, neighbor, color, parent, WHITE, GRAY, BLACK)
                if result:
                    return result
        color[node] = BLACK
        return None

    # ── ExecutionGraph-aware queries ─────────────────────────────────────────
    # These are the canonical implementations — simulation/ delegates here
    # instead of re-implementing the same algorithms.

    def find_failed_nodes(self, graph: "ExecutionGraph") -> list[str]:
        """Return node_ids that are in FAILED state."""
        from src.monkey_brain.kernel.execute.graph import NodeState

        return [n.id for n in graph.get_step_nodes() if graph.get_state(n.id) == NodeState.FAILED]

    def find_unreachable_nodes(self, graph: "ExecutionGraph") -> list[str]:
        """Return PENDING node_ids whose every direct upstream dep is FAILED."""
        from src.monkey_brain.kernel.execute.graph import NodeState

        result = []
        for node in graph.get_step_nodes():
            state = graph.get_state(node.id)
            if state in (NodeState.COMPLETE, NodeState.FAILED, NodeState.RUNNING):
                continue
            deps = [e.src for e in graph.incoming(node.id) if e.rel == "depends_on"]
            if deps and all(graph.get_state(d) == NodeState.FAILED for d in deps):
                result.append(node.id)
        return result

    def detect_cycles(self, graph: "ExecutionGraph") -> list[list[str]]:
        """Return cycles found via DFS (list of node_id lists); empty = acyclic."""
        step_ids = {n.id for n in graph.get_step_nodes()}
        adj = {nid: [] for nid in step_ids}
        for nid in step_ids:
            for e in graph.outgoing(nid):
                if e.rel == "depends_on" and e.dst in step_ids:
                    adj[nid].append(e.dst)
        has_cycle, cycle = self._find_cycle(adj)
        return [cycle] if has_cycle and cycle else ([] if not has_cycle else [[]])

    def critical_path(self, graph: "ExecutionGraph") -> list[str]:
        """Return node_ids root→leaf of the longest dependency chain (memoised DFS)."""
        step_ids = {n.id for n in graph.get_step_nodes()}
        memo: dict[str, int] = {}

        def depth(nid: str) -> int:
            if nid in memo:
                return memo[nid]
            deps = [e.src for e in graph.incoming(nid) if e.rel == "depends_on" and e.src in step_ids]
            memo[nid] = 1 + max((depth(d) for d in deps), default=0)
            return memo[nid]

        for nid in step_ids:
            depth(nid)
        if not memo:
            return []

        path: list[str] = []
        current: str | None = max(memo, key=lambda k: memo[k])
        while current:
            path.append(current)
            deps = [e.src for e in graph.incoming(current) if e.rel == "depends_on" and e.src in step_ids]
            current = max(deps, key=lambda k: memo.get(k, 0)) if deps else None
        return list(reversed(path))

    def _topological_sort(self, graph: dict) -> list[str] | None:
        visited = set()
        order = []
        visiting = set()

        def dfs(node):
            if node in visiting:
                return False
            if node in visited:
                return True
            visiting.add(node)
            for neighbor in graph.get(node, []):
                if not dfs(neighbor):
                    return False
            visiting.remove(node)
            visited.add(node)
            order.append(node)
            return True

        for node in graph:
            if not dfs(node):
                return None
        return list(reversed(order))
