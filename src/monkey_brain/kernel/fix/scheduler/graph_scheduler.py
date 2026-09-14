"""Graph Scheduler — walks the execution graph.

The scheduler is no longer:
    for step in workload:
        execute(step)

It's:
    while graph.has_runnable_nodes():
        Find every node whose dependencies are satisfied.
        Execute it.
        Update graph.
        Repeat.

This naturally supports:
    - Branching (multiple runnable nodes)
    - Parallelism (independent nodes)
    - Retries (re-inject failed nodes)
    - Self-healing (graph expansion on failure)
    - Human approval (inject approval nodes)
    - Recursive workloads (inject sub-workload nodes)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from src.monkey_brain.kernel.execute.graph import ExecutionGraph, GraphNode, NodeState

logger = logging.getLogger(__name__)

ExecutionMode = Literal["serial", "parallel"]


class GraphScheduler:
    """Walks the execution graph, executing runnable nodes.

    The engine's only responsibility:
        1. Find runnable nodes
        2. Execute them
        3. Update graph state
        4. Check expansion policy
        5. Repeat

    `execution_mode` controls how multiple *simultaneously* runnable nodes
    within one tick are executed:
        "serial"   (default) — one at a time, in order. Required when the
                    capability bus routes to a backend that can't handle
                    concurrent requests (e.g. a local Ollama instance — it
                    serializes/chokes on parallel generation calls).
        "parallel" — all runnable nodes in the tick fired concurrently via
                    asyncio.gather(). Safe only when every capability the
                    graph might dispatch to tolerates concurrent calls (a
                    hosted API backend, stateless HTTP capabilities, etc.).
    Defaults to "serial" so behavior is unchanged unless a caller opts in.
    """

    def __init__(
        self,
        bus: Any = None,
        max_iterations: int = 10,
        expansion_policy: Any = None,
        execution_mode: ExecutionMode = "serial",
        step_timeout: float | None = 300.0,
    ):
        self._bus = bus
        self._max_iterations = max_iterations
        self._expansion_policy = expansion_policy
        self._execution_mode = execution_mode
        # Bound every capability call. Capabilities routinely route to a local Ollama /
        # network backend; without a timeout a single hung handler blocks the tick — and
        # therefore the whole workload — forever. None disables the bound.
        self._step_timeout = step_timeout

    async def run(self, graph: ExecutionGraph) -> ExecutionGraph:
        """Walk the graph until no runnable nodes remain or max iterations hit.

        Returns the graph with updated execution states.
        """
        iteration = 0

        while graph.has_runnable_nodes() and iteration < self._max_iterations:
            iteration += 1
            await self.run_one_tick(graph, iteration=iteration)

        if iteration >= self._max_iterations:
            logger.warning("Scheduler hit max iterations (%d)", self._max_iterations)

        return graph

    async def run_one_tick(self, graph: ExecutionGraph, iteration: int = 0) -> int:
        """Execute exactly one scheduling round: every currently-runnable node,
        then a single expansion check. Returns the number of nodes executed.

        Extracted from run()'s loop body so callers that need to observe or
        control graph state *between* rounds (e.g. a process manager that
        must be able to suspend/checkpoint mid-execution) can drive the
        scheduler incrementally instead of only run-to-completion. run()
        itself is unchanged in behavior — it just calls this in a loop.
        """
        runnable = graph.get_runnable_nodes()

        logger.info(
            "Scheduler iteration %d: %d runnable nodes (mode=%s)",
            iteration,
            len(runnable),
            self._execution_mode,
        )

        if self._execution_mode == "parallel":
            await asyncio.gather(*(self._execute_node(graph, node) for node in runnable))
        else:
            for node in runnable:
                await self._execute_node(graph, node)

        # Check if graph should expand (e.g., on failure)
        self._maybe_expand(graph)

        return len(runnable)

    async def _execute_node(self, graph: ExecutionGraph, node: GraphNode) -> None:
        """Execute a single node and update graph state."""
        graph.mark_running(node.id)

        if node.type == "approval_gate":
            # Deliberately left RUNNING, not auto-completed like a metadata
            # node — an approval_gate blocks its dependents until an external
            # actor (e.g. ProcessManager.approve()) explicitly marks it
            # complete/failed. The scheduler itself has no concept of human
            # approval; this is the one seam it exposes for that.
            return

        try:
            if node.type == "step":
                result = await self._execute_step(graph, node)
            elif node.type == "workload":
                result = {"status": "workload_container"}
            else:
                result = {"status": "metadata_node"}

            if result.get("status") in ("failed", "error"):
                # _execute_step reports a capability's success=False (or its
                # own internal exception) as a normal dict return rather than
                # raising, so it must be checked explicitly here — otherwise
                # a capability that fails without raising would silently be
                # marked COMPLETE, which is what happened before this check
                # existed (nothing downstream ever reads this "status" key on
                # a COMPLETE node, confirming it was never actually acted on).
                graph.mark_failed(node.id, str(result.get("error", "")))
            else:
                graph.mark_complete(node.id, result)
                # A repair-chain's "verify" node declares which originally-
                # failed node it supersedes (see SelfHealingPolicy) — once
                # verify succeeds, the original node's dependents must be
                # able to proceed too, so propagate completion to it.
                supersedes = node.props.get("supersedes")
                if supersedes and graph.get_state(supersedes) == NodeState.FAILED:
                    graph.mark_complete(supersedes, result)

        except Exception as e:
            logger.error("Node %s failed: %s", node.id, e)
            graph.mark_failed(node.id, str(e))

    async def _execute_step(self, graph: ExecutionGraph, node: GraphNode) -> dict[str, Any]:
        """Execute a step node via the capability bus."""
        from src.monkey_brain.kernel.pipeline.graph_execution import (
            apply_runtime_projections,
            step_bus_args,
        )

        capability_name = node.props.get("capability", node.label)
        bus_args = step_bus_args(node, graph.metadata if isinstance(graph.metadata, dict) else None)

        if self._bus is None:
            return {"status": "no_bus", "capability": capability_name}

        try:
            call = self._bus.execute(capability_name, bus_args)
            if self._step_timeout is not None:
                result = await asyncio.wait_for(call, timeout=self._step_timeout)
            else:
                result = await call
            payload = {
                "status": "ok" if result.success else "failed",
                "output": result.output,
                "error": result.error,
            }
            if result.success and isinstance(result.output, dict):
                projections = node.props.get("runtime_projections") or []
                context = bus_args.get("context")
                if isinstance(context, dict) and projections:
                    apply_runtime_projections(result.output, context, projections)
            return payload
        except asyncio.TimeoutError:
            logger.error(
                "Step %s: capability %r exceeded %ss — cancelled",
                node.id,
                capability_name,
                self._step_timeout,
            )
            return {
                "status": "error",
                "error": f"capability_timeout after {self._step_timeout}s",
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _maybe_expand(self, graph: ExecutionGraph) -> None:
        """Check expansion policy and expand graph if needed.

        This is where self-healing, retries, and approval injection happen.
        The policy examines failed nodes and decides what to inject.
        """
        if self._expansion_policy is None:
            return

        failed_nodes = [node for node in graph.get_step_nodes() if graph.get_state(node.id) == NodeState.FAILED]

        if not failed_nodes:
            return

        for node in failed_nodes:
            expansion = self._expansion_policy.on_failure(graph, node)
            if expansion is not None:
                graph.expand(expansion["nodes"], expansion["edges"])
