"""Contract for external (SittingFace) knowledge before it enters an LLM prompt."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.pipeline.planning.domain import RetrievedItem


@dataclass(frozen=True)
class ExternalKnowledgeItem:
    """One retrieved external fact with provenance — not authoritative world state."""

    content: str
    source_chart: str = ""
    source_path: str = ""
    retrieval_method: str = "keyword"  # keyword | vector | keyword_fallback
    relevance_score: float = 0.0
    query: str = ""
    matched_fields: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_retrieved_item(self) -> RetrievedItem:
        method = self.retrieval_method or "keyword"
        source = f"sittingface:{self.source_chart}" if self.source_chart else "sittingface"
        return RetrievedItem(
            content=self.content,
            item_type="external_knowledge",
            source=source,
            confidence=min(1.0, max(0.0, self.relevance_score or 0.7)),
            retrieval_score=self.relevance_score,
            evidence_ids=(self.source_chart,) if self.source_chart else (),
        )


@dataclass
class KnowledgeRetrievalReport:
    """Structured result of a SittingFace retrieval attempt."""

    query: str
    attempted: bool = False
    items: list[ExternalKnowledgeItem] = field(default_factory=list)
    methods_used: list[str] = field(default_factory=list)
    vector_available: bool = False
    vector_used: bool = False
    keyword_used: bool = False
    latency_ms: float = 0.0
    error: str = ""
    injected: bool = False
    cache_hit: bool = False

    def to_retrieved_items(self) -> tuple[RetrievedItem, ...]:
        return tuple(item.to_retrieved_item() for item in self.items)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "external_knowledge_retrieval": {
                "attempted": self.attempted,
                "query": self.query,
                "methods_used": list(self.methods_used),
                "vector_available": self.vector_available,
                "vector_used": self.vector_used,
                "keyword_used": self.keyword_used,
                "result_count": len(self.items),
                "sources": [i.source_chart for i in self.items if i.source_chart],
                "latency_ms": round(self.latency_ms, 3),
                "injected": self.injected,
                "cache_hit": self.cache_hit,
                "error": self.error or None,
            },
        }

    def format_for_prompt(self, *, max_items: int = 5, max_chars: int = 2400) -> str:
        """Render retrieved knowledge for inclusion in a compiled prompt."""
        if not self.items:
            return ""
        lines = [
            "## External Knowledge (SittingFace)",
            "The following is retrieved reference material — not authoritative world state.",
            "",
        ]
        used = 0
        total = 0
        for item in self.items[:max_items]:
            line = (
                f"- [{item.source_chart or 'chart'} | {item.retrieval_method}"
                f" score={item.relevance_score:.2f}] {item.content}"
            )
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)
            used += 1
        if used < len(self.items):
            lines.append(f"- ... ({len(self.items) - used} more matches omitted)")
        return "\n".join(lines)
