"""Knowledge Manager - Single Responsibility.

Responsibility: Knowledge base exploration, gap detection, acquisition, grounding.
Depends on: semantic memory, knowledge acquisition, intent registry
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentos.cognitive.layers.knowledge_manager")


class KnowledgeManager:
    """Knowledge exploration, gap detection, acquisition, and grounding.

    Responsibility: Query external knowledge bases (SittingFace), detect
    knowledge gaps, acquire missing knowledge, ground existing knowledge.
    """

    def __init__(self) -> None:
        self._semantic_memory: Any = None

    def set_semantic_memory(self, semantic_memory: Any) -> None:
        self._semantic_memory = semantic_memory

    def explore_knowledge_base(self, query: str | None = None) -> Any:
        """Query SittingFace as an external knowledge-base repo.

        Returns the compiler's summary when no query is given;
        returns None if SittingFace never loaded.
        """
        from src.monkey_brain.kernel.plan.intents.intent_registry import (
            get_somatic_compiler,
        )

        compiler = get_somatic_compiler()
        if compiler is None:
            return None
        if query is None:
            return compiler.summary()
        if hasattr(compiler, "search"):
            return compiler.search(query)
        return compiler.summary()

    def find_knowledge_gaps(self, question: str, knowledge: dict) -> list[str]:
        """Identify what we don't know about the question."""
        gaps = []
        results = knowledge.get("results", [])
        if not results:
            gaps.append(f"No knowledge found for: {question}")

        if "todo" in question.lower() or "task" in question.lower():
            if not any("todo" in str(r).lower() or "task" in str(r).lower() for r in results):
                gaps.append("No todo/task management knowledge found")

        if any(w in question.lower() for w in ["google docs", "gdocs", "google drive"]):
            if not any("google" in str(r).lower() for r in results):
                gaps.append("No Google Docs integration knowledge found")

        return gaps

    async def acquire_knowledge(self, gaps: list[str], question: str) -> dict:
        """Acquire missing knowledge from available sources.

        Runs the real acquisition service: query available sources,
        materialize retrieved snippets as KnowledgeItems.
        """
        from src.monkey_brain.kernel.plan.knowledge_acquisition import acquire_knowledge
        from src.monkey_brain.kernel.cognitive_kernel import get_cognitive_kernel

        try:
            result = await acquire_knowledge(
                question,
                kernel=get_cognitive_kernel(),
                semantic_memory=self._semantic_memory,
            )
        except Exception as exc:
            logger.warning("[acquire] knowledge acquisition failed: %s", exc)
            result = {
                "question": question,
                "acquired": 0,
                "raised": False,
                "error": str(exc),
            }

        return result

    async def ground_knowledge(self, knowledge: dict, question: str) -> None:
        """Ground existing knowledge for the current question."""
        pass

    async def query_semantic_memory(self, question: str) -> dict:
        """Query semantic memory for relevant knowledge."""
        if self._semantic_memory and self._semantic_memory.available:
            return await self._semantic_memory.query(question) or {}
        return {}

    def get_semantic_memory_summary(self) -> dict:
        """Get semantic memory summary."""
        if self._semantic_memory:
            return self._semantic_memory.summary()
        return {}
