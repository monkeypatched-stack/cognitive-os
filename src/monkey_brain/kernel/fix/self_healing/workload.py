"""Self-Healing Expansion Policy — graph expansion on failure.

Instead of:
    if failed:
        repair()

The runtime performs:
    Graph.expand(
        RepairWorkload(...)
    )

Repair is not a special case. It injects another workload into the execution graph.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.execute.graph import (
    ExecutionGraph,
    GraphNode,
    GraphEdge,
)
from src.monkey_brain.kernel.plan.workload.workload import Workload, WorkloadStep

logger = logging.getLogger(__name__)


class SelfHealingPolicy:
    """Expands the graph with repair nodes when steps fail.

    Bounded by max_repair_attempts to prevent runaway expansion.
    """

    def __init__(self, max_repair_attempts: int = 3):
        self._max_attempts = max_repair_attempts
        self._attempt_count: dict[str, int] = {}

    def on_failure(self, graph: ExecutionGraph, failed_node: GraphNode) -> dict[str, Any] | None:
        """Called when a node fails. Returns expansion nodes/edges or None.

        Expands the graph with a retry-then-recheck of the SAME capability the
        failed node was already running (resolved the same way GraphScheduler
        resolves it: `props["capability"]` falling back to `label`). This is a
        graph expansion, not a planning decision — it must never invent a new
        capability the failed node wasn't already configured to use, since
        nothing registers that capability and the injected node would just
        fail again on dispatch.
        """
        node_id = failed_node.id
        capability = failed_node.props.get("capability", failed_node.label)

        # A repair/verify node that itself fails must NOT start its own
        # fresh budget — otherwise every retry of the same root failure
        # spawns another independent repair-of-repair-of-repair chain
        # (repair-1 fails → repair-repair-1-1 → repair-repair-repair-1-1-1
        # → ...), which is unbounded even though each individual node's
        # own budget check looks bounded. All retries of one underlying
        # failure must share one budget, so trace back to the original
        # (non-repair) node this chain started from.
        root_id = failed_node.props.get("repair_root", node_id)

        # Check repair budget — keyed by root_id, not node_id, so the whole
        # chain (root + every repair/verify spawned for it) shares one cap.
        attempts = self._attempt_count.get(root_id, 0)
        if attempts >= self._max_attempts:
            logger.info(
                "Node %s hit repair budget (%d/%d) — no expansion",
                root_id,
                attempts,
                self._max_attempts,
            )
            return None

        self._attempt_count[root_id] = attempts + 1

        # Generate repair nodes — both reuse the failed node's own capability
        repair_id = f"step:repair-{root_id.split(':')[-1]}-{attempts + 1}"
        verify_id = f"step:verify-{root_id.split(':')[-1]}-{attempts + 1}"

        nodes = [
            GraphNode(
                id=repair_id,
                type="step",
                label=f"repair-{attempts + 1}",
                props={
                    "capability": capability,
                    "repair_attempt": attempts + 1,
                    "repair_root": root_id,
                },
            ),
            GraphNode(
                id=verify_id,
                type="step",
                label=f"verify-{attempts + 1}",
                # supersedes: when this node completes successfully, the
                # scheduler also marks root_id COMPLETE — otherwise root_id
                # stays FAILED forever (nothing else ever re-checks it) and
                # every node that depends_on it stays permanently blocked
                # even after the repair has actually succeeded.
                props={
                    "capability": capability,
                    "repair_attempt": attempts + 1,
                    "repair_root": root_id,
                    "supersedes": root_id,
                },
            ),
        ]

        # Repair depends on whatever made the ORIGINAL (root) node runnable
        # in the first place — NOT on the failed node itself. A FAILED node
        # never transitions to COMPLETE, and get_runnable_nodes() only
        # considers a dependency satisfied once its source is COMPLETE, so
        # depending on node_id would leave repair_id permanently unrunnable
        # and silently burn the whole repair budget doing nothing. Always
        # resolving against root_id (rather than node_id, which may itself
        # be a prior repair node) keeps every attempt in the chain anchored
        # to the same real preconditions.
        preconditions = [edge.src for edge in graph.incoming(root_id) if edge.rel == "depends_on"]
        edges = [GraphEdge(src=dep, dst=repair_id, rel="depends_on") for dep in preconditions] + [
            # Repair → verify (recheck the retry)
            GraphEdge(src=repair_id, dst=verify_id, rel="depends_on"),
        ]

        logger.info(
            "Expanding graph: injecting repair node %s (capability=%s) after %s failed (root=%s)",
            repair_id,
            capability,
            node_id,
            root_id,
        )

        return {"nodes": nodes, "edges": edges}


def create_self_healing_workload() -> Workload:
    """Create the built-in self-healing workload template."""
    return Workload(
        workload_id="self-healing",
        steps=[
            WorkloadStep(
                step_id="self-healing-1",
                capability_name="self_healing",
                inputs=["question", "context"],
                outputs=["answer"],
                metadata={"template": "self-healing"},
            )
        ],
        metadata={"template": "self-healing"},
    )
