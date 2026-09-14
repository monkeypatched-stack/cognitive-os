"""Graph Pipeline — chains Intent Compiler → Graph Synthesizer.

Pipeline:
    Natural Language
           │
           ▼
    Intent Compiler (LLM)
           │
           ▼
    Goal IR
           │
           ▼
    Graph Synthesizer (LLM)
           │
           ▼
    Candidate Execution Graphs
           │
           ▼
    World Compiler (validation)
           │
           ▼
    Validated Execution Graph
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import BaseETASSAgent
from .intent_compiler_agent import IntentCompilerAgent
from .graph_generator_agent import GraphGeneratorAgent

logger = logging.getLogger("broca.agents.graph_pipeline")


class GraphPipeline(BaseETASSAgent):
    """Full pipeline: Intent Compiler → Graph Synthesizer.

    This is the two-stage pipeline that separates intent compilation
    from graph generation, producing a Goal IR intermediate step.
    """

    agent_type = "graph_pipeline"
    description = "Full pipeline: Intent Compiler → Graph Synthesizer"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._intent_compiler = IntentCompilerAgent()
        self._graph_generator = GraphGeneratorAgent()

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        intent = context.get("intent") or context.get("question") or context.get("specification", {}).get("goal", "")

        if not intent:
            self._reward(False, 0.0)
            return self._result(
                payload={"error": "No intent provided"},
                observations=["Pipeline received empty intent"],
            )

        # Stage 1: Intent Compiler → Goal IR
        logger.info("[pipeline] Stage 1: Compiling intent to Goal IR")
        goal_ir_context = {
            "intent": intent,
            "question": intent,
            "specification": context.get("specification", {}),
        }
        goal_ir = await self._intent_compiler._impl(goal_ir_context)

        logger.info(
            "[pipeline] Goal IR: type=%s, domain=%s, entities=%d, constraints=%d",
            goal_ir.intent_type,
            goal_ir.domain,
            len(goal_ir.entities),
            len(goal_ir.constraints),
        )

        # Stage 2: Graph Synthesizer → Candidate Graphs
        logger.info("[pipeline] Stage 2: Generating execution graphs from Goal IR")
        graph_context = {
            "goal_ir": goal_ir,
            "intent": intent,
            "question": intent,
            "specification": context.get("specification", {}),
            "knowledge": context.get("knowledge", {}),
        }
        graph_result = await self._graph_generator._impl(graph_context)

        # Stage 3: World Compiler (validation) — placeholder for future
        # For now, the GraphGeneratorAgent already validates graphs

        # Combine results
        self._reward(True, 0.8)
        return self._result(
            payload={
                "goal_ir": goal_ir.to_dict(),
                "candidates": graph_result.payload.get("candidates", []),
                "agents": graph_result.payload.get("agents", []),
                "agent_map": graph_result.payload.get("agent_map", {}),
                "node_count": graph_result.payload.get("node_count", 0),
                "edge_count": graph_result.payload.get("edge_count", 0),
            },
            observations=[
                f"Stage 1 (Intent Compiler): {goal_ir.intent_type}/{goal_ir.domain}",
                f"Stage 2 (Graph Synthesizer): {len(graph_result.payload.get('candidates', []))} candidates",
            ]
            + graph_result.observations,
        )
