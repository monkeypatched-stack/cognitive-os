"""Semantic retrieval caching for the edge hot path.

Wraps kernel/knowledge/sittingface_retrieval.py::SittingFaceKnowledgeRetriever
-- never reimplements retrieval, never touches kernel/semantic_memory.py's
provenance-correctness fix (retrieval_method is always read from the
underlying KnowledgeRetrievalReport verbatim, never re-derived or
guessed here).

Order for edge operation (Section 9):

    1. local retrieval-result cache (this module)
    2. local semantic index / EmbeddingStore, if available
    3. local keyword retrieval
    4. remote retrieval

This module is purely a cache in front of step 1 -- steps 2-4 are
whatever SittingFaceKnowledgeRetriever already does; this class never
calls Ollama/Elasticsearch itself and never fabricates a vector. A cache
hit here means "we already retrieved for this exact query recently," not
"we skipped verifying whether retrieval would still say the same thing"
-- the TTL bounds how long that assumption is trusted.
"""
from __future__ import annotations

from typing import Any

from src.monkey_brain.kernel.edge.local_cache import BoundedTTLCache

DEFAULT_SEMANTIC_CACHE_TTL_SECONDS = 60.0


def _normalize_query(query: str) -> str:
    return " ".join((query or "").strip().lower().split())


class CachedSittingFaceRetriever:
    """Drop-in in front of a real SittingFaceKnowledgeRetriever."""

    def __init__(self, retriever: Any, *, max_size: int = 256, ttl_seconds: float = DEFAULT_SEMANTIC_CACHE_TTL_SECONDS) -> None:
        self._retriever = retriever
        self._cache: BoundedTTLCache = BoundedTTLCache(max_size=max_size, default_ttl_seconds=ttl_seconds)
        self._ttl = ttl_seconds

    def retrieve_sync(self, query: str, *, cycle_id: str = "", force: bool = False, knowledge_version: str = ""):
        key = f"{_normalize_query(query)}|{cycle_id}"
        version_key = knowledge_version
        if not force:
            cached = self._cache.get(key, version_key=version_key)
            if cached is not None:
                report = _copy_report_as_cache_hit(cached)
                return report
        report = self._retriever.retrieve_sync(query, cycle_id=cycle_id, force=force)
        if report.attempted:
            self._cache.put(key, report, version_key=version_key, ttl_seconds=self._ttl)
        return report

    async def retrieve(self, query: str, *, cycle_id: str = "", force: bool = False, meta: dict | None = None, knowledge_version: str = ""):
        key = f"{_normalize_query(query)}|{cycle_id}"
        version_key = knowledge_version
        if not force:
            cached = self._cache.get(key, version_key=version_key)
            if cached is not None:
                return _copy_report_as_cache_hit(cached)
        report = await self._retriever.retrieve(query, cycle_id=cycle_id, force=force, meta=meta)
        if report.attempted:
            self._cache.put(key, report, version_key=version_key, ttl_seconds=self._ttl)
        return report

    def stats(self) -> dict[str, Any]:
        return self._cache.stats()


def _copy_report_as_cache_hit(report: Any) -> Any:
    """Returns a shallow copy of the cached KnowledgeRetrievalReport with
    cache_hit=True and retrieval_method/vector_used/methods_used copied
    VERBATIM from the original retrieval -- a cache hit must report the
    SAME provenance the real retrieval established, never re-labeled as
    something else merely because it came from this module's own cache."""
    from dataclasses import replace
    return replace(report, cache_hit=True)
