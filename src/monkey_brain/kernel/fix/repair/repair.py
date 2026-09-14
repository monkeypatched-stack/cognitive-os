"""Fix phase — produce an updated ExecutionGraph when the predict phase surfaces issues.

Repair strategies per issue kind
──────────────────────────────────
FAILED_NODE         → delegate to SelfHealingPolicy; inject repair+verify nodes
UNREACHABLE_NODE    → mark SKIPPED so the execute phase bypasses the node
CRITICAL_PATH_SLOW  → same as FAILED_NODE (critical path gets priority budget)
CYCLE_DETECTED      → break the cycle by removing the back-edge to the deepest node
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RepairResult:
    issues_addressed: int = 0
    nodes_injected: int = 0
    nodes_skipped: int = 0
    cycles_broken: int = 0

    @property
    def any_change(self) -> bool:
        return (self.nodes_injected + self.nodes_skipped + self.cycles_broken) > 0


class DAGRepairer:
    """Applies targeted repairs to an ExecutionGraph based on Issue list."""

    def repair(self, graph: Any, issues: list[Any]) -> RepairResult:
        """Mutate graph in-place; return summary of changes made."""
        from src.monkey_brain.kernel.predict.simulation import IssueKind
        from src.monkey_brain.kernel.execute.graph import NodeState

        result = RepairResult()
        seen: set[str] = set()

        for issue in issues:
            if issue.node_id in seen:
                continue

            if issue.kind in (IssueKind.FAILED_NODE, IssueKind.CRITICAL_PATH_SLOW):
                node = graph.get_node(issue.node_id)
                if node is not None:
                    expansion = self._self_healing(graph, node)
                    if expansion:
                        graph.expand(expansion["nodes"], expansion["edges"])
                        result.nodes_injected += len(expansion["nodes"])
                        seen.add(issue.node_id)

            elif issue.kind == IssueKind.UNREACHABLE_NODE:
                graph.set_state(issue.node_id, NodeState.SKIPPED)
                result.nodes_skipped += 1
                seen.add(issue.node_id)

            elif issue.kind == IssueKind.CYCLE_DETECTED:
                broken = self._break_cycle(graph)
                result.cycles_broken += broken

            result.issues_addressed += 1

        logger.debug(
            "[fix] %d issues addressed — injected=%d skipped=%d cycles_broken=%d",
            result.issues_addressed,
            result.nodes_injected,
            result.nodes_skipped,
            result.cycles_broken,
        )
        return result

    def _self_healing(self, graph: Any, failed_node: Any) -> dict | None:
        try:
            from src.monkey_brain.kernel.fix.self_healing.workload import (
                SelfHealingPolicy,
            )

            policy = SelfHealingPolicy()
            return policy.on_failure(graph, failed_node)
        except Exception as e:
            logger.debug("SelfHealingPolicy failed for %s: %s", failed_node.id, e)
            return None

    def _break_cycle(self, graph: Any) -> int:
        """Remove back-edges by finding nodes with unresolved in-degree in Kahn's sort."""

        step_ids = {n.id for n in graph.get_step_nodes()}
        in_deg = {nid: 0 for nid in step_ids}
        for nid in step_ids:
            for e in graph.incoming(nid):
                if e.rel == "depends_on" and e.src in step_ids:
                    in_deg[nid] += 1

        from collections import deque

        queue: deque[str] = deque(nid for nid, d in in_deg.items() if d == 0)
        visited: set[str] = set()
        while queue:
            nid = queue.popleft()
            visited.add(nid)
            for e in graph.outgoing(nid):
                if e.rel == "depends_on" and e.dst in step_ids:
                    in_deg[e.dst] -= 1
                    if in_deg[e.dst] == 0:
                        queue.append(e.dst)

        # Nodes not visited are part of a cycle — remove their incoming dep edges
        broken = 0
        for nid in step_ids - visited:
            graph._edges = [e for e in graph._edges if not (e.dst == nid and e.rel == "depends_on")]
            if nid in graph._radj:
                graph._radj[nid] = [e for e in graph._radj[nid] if e.rel != "depends_on"]
            broken += 1
            logger.warning("[fix] broke cycle at node %s", nid)

        return broken


class LossDrivenRepair:
    """Wraps DAGRepairer with loss-threshold gating.

    Repair only runs when composite EPA loss exceeds the threshold,
    avoiding unnecessary graph mutations on healthy executions.

    Accepts RepairRequest (the canonical FIX-phase input) which carries both
    PredictionReport and LearningReport so Fix has everything it needs.
    """

    def __init__(self, loss_threshold: float = 0.3):
        self.loss_threshold = loss_threshold
        self._repairer = DAGRepairer()

    def run(self, graph: Any, request: Any) -> RepairResult:
        """Consume a RepairRequest and repair the graph.

        request — RepairRequest with .issues, .composite_loss, .loss_vector,
                  .graph_analysis, .recommended_repairs
        """
        issues = request.issues
        loss = request.composite_loss

        if loss < self.loss_threshold and not any(i.kind in ("failed_node", "cycle_detected") for i in issues):
            logger.debug("[fix] loss=%.3f below threshold — skipping repair", loss)
            return RepairResult()
        return self._repairer.repair(graph, issues)
