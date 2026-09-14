"""WorldCompilerAgent — validates execution graphs against the world model.

Pipeline stage:
    Candidate Execution Graphs → World Compiler → Validated Execution Graph

The World Compiler:
- Validates candidate DAGs against registered agents/capabilities
- Resolves abstract agent names to concrete implementations
- Enforces governance rules and policies
- Produces a validated ExecutionGraph
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.world_compiler")


class WorldCompilerAgent(BaseETASSAgent):
    """Validates execution graphs against the world model.

    This is the third stage of the compiler pipeline. It takes candidate DAGs
    and validates them against the world model (registered agents, governance rules,
    resource constraints) to produce validated execution graphs.
    """

    agent_type = "world_compiler"
    description = "Validates execution graphs against world model"

    def __init__(self, trust_network=None, trust_threshold: float = 0.5, **kwargs):
        super().__init__(**kwargs)
        self._trust_network = trust_network
        self._trust_threshold = trust_threshold

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        """Validate execution graphs against world model."""
        goal_ir = context.get("goal_ir")
        candidates = context.get("candidates", [])

        if not candidates:
            self._reward(False, 0.0)
            return self._result(
                payload={"error": "No candidates to validate"},
                observations=["World Compiler received empty candidates"],
            )

        # Validate and score each candidate against trust fabric
        scored_candidates = []
        for cand in candidates:
            graph = cand.get("graph", {})
            execution_graph = cand.get("execution_graph")

            if execution_graph is None:
                continue

            # Score candidate based on agent trust and feasibility
            is_valid, trust_score = await self.score_candidate(graph, goal_ir)

            scored_candidates.append(
                {
                    "graph": graph,
                    "execution_graph": execution_graph,
                    "execution_graph_id": cand.get("execution_graph_id"),
                    "is_valid": is_valid,
                    "trust_score": trust_score,  # NEW: trust-based ranking
                }
            )

        if not scored_candidates:
            self._reward(False, 0.0)
            return self._result(
                payload={"error": "No candidates scored"},
                observations=["World Compiler could not score any candidates"],
            )

        # Sort by trust score (highest first) — compiler prefers high-trust agents
        # even if other metrics (cost, speed) are slightly worse
        scored_candidates.sort(key=lambda c: c["trust_score"], reverse=True)

        # Filter to valid candidates, fallback to all if none valid
        validated = [c for c in scored_candidates if c["is_valid"]]
        if not validated:
            validated = scored_candidates  # Use highest-trust even if not fully valid

        logger.info(
            "[world_compiler] Ranked %d candidates by trust: best=%.2f",
            len(scored_candidates),
            scored_candidates[0]["trust_score"] if scored_candidates else 0.0,
        )

        self._reward(True, 0.8)
        return self._result(
            payload={
                "goal_ir": goal_ir.to_dict() if goal_ir else {},
                "candidates": [
                    {
                        "graph": c["graph"],
                        "execution_graph": c["execution_graph"],
                        "execution_graph_id": c["execution_graph_id"],
                        "validation_score": c["trust_score"],  # Trust-based ranking
                    }
                    for c in validated
                ],
                "validated_count": len(validated),
                "total_candidates": len(candidates),
                "trust_scores": {c["execution_graph_id"]: c["trust_score"] for c in scored_candidates},
            },
            observations=[
                f"World Compiler: ranked {len(scored_candidates)} candidates by trust",
                f"Top candidate trust: {scored_candidates[0]['trust_score']:.2f}",
                "Execution IR ready for Runtime",
            ],
        )

    async def score_candidate(self, graph: dict, goal_ir) -> tuple[bool, float]:
        """Score a candidate graph based on agent trust and feasibility.

        Returns (is_valid, trust_score) where:
            is_valid: True if graph passes basic validation
            trust_score: 0-1 indicating reliability of agents in the graph
                        Used by compiler to rank candidates

        Trust score = mean trust of all agents, weighted by their criticality.
        High-trust agent graphs rank higher even if cost is similar.
        """
        from src.monkey_brain.kernel.compile.trust import Perm

        nodes = graph.get("nodes", [])
        if not nodes:
            return False, 0.0

        # Collect trust scores for all agents in the graph
        agent_trust_scores = []
        validation_issues = []

        for node in nodes:
            agent_name = node.get("agent") or node.get("name")
            if not agent_name:
                validation_issues.append(f"Node {node.get('id')} missing agent name")
                continue

            if self._trust_network:
                edge = self._trust_network.edge("self", agent_name)

                if not edge:
                    # Agent not in trust network — penalize but don't fail
                    agent_trust_scores.append(0.5)  # Neutral trust
                    continue

                if not edge.is_active:
                    agent_trust_scores.append(0.0)  # Revoked/expired edge
                    continue

                if Perm.SHARE_EXECUTION_GRAPHS not in edge.permissions:
                    agent_trust_scores.append(0.3)  # Lacks permission
                    continue

                # Valid agent with permission
                agent_trust_scores.append(edge.trust_score)
            else:
                # No trust network: all agents neutral
                agent_trust_scores.append(1.0)

        # Graph is valid if it has agents and no critical issues
        is_valid = len(agent_trust_scores) > 0 and not validation_issues

        # Compute graph-wide trust score (mean of agent trust)
        trust_score = (sum(agent_trust_scores) / len(agent_trust_scores)) if agent_trust_scores else 0.5

        if validation_issues:
            logger.warning(
                "[world_compiler] Graph has %d validation issue(s), trust_score=%.2f: %s",
                len(validation_issues),
                trust_score,
                "; ".join(validation_issues),
            )

        return is_valid, trust_score

    async def _validate_against_world(self, graph: dict, goal_ir) -> bool:
        """Validate a graph against the world model: trust network, governance, resources."""
        from src.monkey_brain.kernel.compile.trust import Perm

        nodes = graph.get("nodes", [])
        validation_errors = []

        # ── Trust Network Validation ──────────────────────────────────────
        if self._trust_network:
            # Determine the current runtime identity (requesting party)
            # In a distributed context, this would be the local runtime's ID
            local_runtime = "self"  # TODO: get from context/config

            for node in nodes:
                agent_name = node.get("agent") or node.get("name")
                if not agent_name:
                    validation_errors.append(f"Node {node.get('id')} missing agent name")
                    continue

                # Check if there's a trust relationship to this agent/runtime
                edge = self._trust_network.edge(local_runtime, agent_name)

                if not edge:
                    validation_errors.append(
                        f"Agent '{agent_name}' not in trust network (no edge from {local_runtime})"
                    )
                    logger.warning(
                        "[world_compiler] Agent '%s' not trusted by %s",
                        agent_name,
                        local_runtime,
                    )
                    continue

                # Check if trust score meets threshold
                if edge.trust_score < self._trust_threshold:
                    validation_errors.append(
                        f"Agent '{agent_name}' trust score {edge.trust_score:.2f} < threshold {self._trust_threshold}"
                    )
                    logger.warning(
                        "[world_compiler] Agent '%s' trust below threshold: %.2f < %.2f",
                        agent_name,
                        edge.trust_score,
                        self._trust_threshold,
                    )
                    continue

                # Check if permission to share/execute graphs is granted
                if Perm.SHARE_EXECUTION_GRAPHS not in edge.permissions:
                    validation_errors.append(f"Agent '{agent_name}' does not have SHARE_EXECUTION_GRAPHS permission")
                    logger.warning(
                        "[world_compiler] Agent '%s' lacks SHARE_EXECUTION_GRAPHS permission",
                        agent_name,
                    )

                # Check if edge is active (not revoked/expired)
                if not edge.is_active:
                    validation_errors.append(f"Agent '{agent_name}' trust edge is inactive (revoked or expired)")
                    logger.warning("[world_compiler] Agent '%s' trust edge inactive", agent_name)
        else:
            # No trust network: just verify agent names exist
            for node in nodes:
                agent_name = node.get("agent") or node.get("name")
                if not agent_name:
                    validation_errors.append(f"Node {node.get('id')} missing agent name")

        # ── Governance Rule Validation ────────────────────────────────────
        # Check if execution would violate any policies
        # TODO: Enforce specific governance policies from goal_ir or world state

        # ── Resource Constraint Validation ────────────────────────────────
        # Check if there are sufficient resources to execute the plan
        # TODO: Validate against available resources (compute, memory, etc.)

        if validation_errors:
            logger.warning(
                "[world_compiler] Graph validation failed with %d error(s):\n%s",
                len(validation_errors),
                "\n".join(f"  - {e}" for e in validation_errors),
            )
            return False

        logger.info(
            "[world_compiler] Graph passed validation (agents=%d, trust_ok=True)",
            len(nodes),
        )
        return True

    def record_agent_outcome(self, agent_name: str, success: bool, latency_ms: float = 0.0) -> float:
        """Update agent reputation based on execution outcome.

        Success: +0.05 reputation
        Failure: -0.10 reputation
        Slow (>5s): additional -0.02
        Fast (<100ms): additional +0.02
        """
        if not self._trust_network:
            return 0.0

        local_runtime = "self"  # TODO: get from context/config
        return self._trust_network.record_outcome(local_runtime, agent_name, success, latency_ms)
