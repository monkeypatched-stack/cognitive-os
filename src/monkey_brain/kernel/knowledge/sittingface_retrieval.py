"""SittingFace knowledge retrieval — keyword + optional vector, with cycle cache."""
from __future__ import annotations

import contextvars
import logging
import re
import time
from typing import Any

from src.monkey_brain.kernel.knowledge.external_context import (
    ExternalKnowledgeItem,
    KnowledgeRetrievalReport,
)

logger = logging.getLogger("agentos.knowledge.sittingface")

_RETRIEVAL_CACHE: contextvars.ContextVar[dict[str, KnowledgeRetrievalReport] | None] = contextvars.ContextVar(
    "sittingface_retrieval_cache", default=None,
)

_KNOWLEDGE_QUERY_PATTERNS = re.compile(
    r"\b(what|how|why|explain|describe|architecture|compliance|sop|capa|oee|"
    r"gmp|regulation|policy|specification|chart|agent|workload|etass|"
    r"knowledge|document|procedure|standard)\b",
    re.IGNORECASE,
)

_STOPWORDS = frozenset({
    "a", "an", "the", "for", "to", "of", "and", "or", "in", "on", "at", "is", "are",
    "was", "were", "this", "that", "i", "we", "you", "please", "buy", "purchase", "order",
})


def should_retrieve_external_knowledge(
    query: str,
    *,
    meta: dict[str, Any] | None = None,
    min_content_tokens: int = 2,
) -> bool:
    """Deterministic policy for when SittingFace retrieval runs."""
    meta = meta or {}
    if meta.get("skip_external_knowledge"):
        return False
    if meta.get("include_external_knowledge"):
        return True
    text = (query or "").strip()
    if not text:
        return False
    tokens = {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS and len(t) > 2}
    if len(tokens) >= min_content_tokens and _KNOWLEDGE_QUERY_PATTERNS.search(text):
        return True
    if len(tokens) >= 4:
        return True
    return False


def _cache_key(cycle_id: str, query: str) -> str:
    normalized = re.sub(r"\s+", " ", (query or "").strip().lower())
    return f"{cycle_id or 'global'}:{normalized}"


def _get_cache() -> dict[str, KnowledgeRetrievalReport]:
    cache = _RETRIEVAL_CACHE.get()
    if cache is None:
        cache = {}
        _RETRIEVAL_CACHE.set(cache)
    return cache


class SittingFaceKnowledgeRetriever:
    """Retrieve SittingFace chart knowledge for prompt augmentation."""

    def __init__(self, semantic_memory: Any = None) -> None:
        self._semantic_memory = semantic_memory

    def set_semantic_memory(self, semantic_memory: Any) -> None:
        self._semantic_memory = semantic_memory

    def retrieve_sync(self, query: str, *, cycle_id: str = "", force: bool = False) -> KnowledgeRetrievalReport:
        """Keyword-only retrieval — safe for synchronous context assembly."""
        if not force and not should_retrieve_external_knowledge(query):
            return KnowledgeRetrievalReport(query=query, attempted=False)
        return self._retrieve_core(query, cycle_id=cycle_id, allow_vector=False)

    async def retrieve(
        self,
        query: str,
        *,
        cycle_id: str = "",
        force: bool = False,
        meta: dict[str, Any] | None = None,
    ) -> KnowledgeRetrievalReport:
        """Keyword + optional vector retrieval."""
        if not force and not should_retrieve_external_knowledge(query, meta=meta):
            return KnowledgeRetrievalReport(query=query, attempted=False)
        return await self._retrieve_core_async(query, cycle_id=cycle_id)

    def _retrieve_core(self, query: str, *, cycle_id: str, allow_vector: bool) -> KnowledgeRetrievalReport:
        started = time.perf_counter()
        key = _cache_key(cycle_id, query)
        cache = _get_cache()
        if key in cache:
            return _copy_report(cache[key], cache_hit=True)

        report = KnowledgeRetrievalReport(query=query, attempted=True)
        items, methods = self._keyword_pass(query)
        report.keyword_used = "keyword" in methods
        report.items = items
        report.methods_used = methods

        semantic_memory = self._resolve_semantic_memory()
        report.vector_available = bool(semantic_memory and getattr(semantic_memory, "available", False))
        if allow_vector and report.vector_available:
            logger.debug("[sittingface_retrieval] vector skipped in sync path — use async retrieve()")

        if not report.vector_available and report.keyword_used:
            report.methods_used = list(dict.fromkeys([*report.methods_used, "keyword_fallback"]))

        report.items = self._finalize_items(report.items)
        report.injected = bool(report.items)
        report.latency_ms = (time.perf_counter() - started) * 1000
        self._log_report(report)
        cache[key] = report
        return report

    async def _retrieve_core_async(self, query: str, *, cycle_id: str) -> KnowledgeRetrievalReport:
        started = time.perf_counter()
        key = _cache_key(cycle_id, query)
        cache = _get_cache()
        if key in cache:
            return _copy_report(cache[key], cache_hit=True)

        report = KnowledgeRetrievalReport(query=query, attempted=True)
        items, methods = self._keyword_pass(query)
        report.keyword_used = "keyword" in methods

        semantic_memory = self._resolve_semantic_memory()
        report.vector_available = bool(semantic_memory and getattr(semantic_memory, "available", False))

        if report.vector_available and semantic_memory is not None:
            try:
                vector_items = await self._vector_retrieve(
                    semantic_memory, query, existing_charts={i.source_chart for i in items},
                )
                if vector_items:
                    items.extend(vector_items)
                    # vector_used reflects what ACTUALLY happened, per item
                    # -- never inferred merely from _vector_retrieve()
                    # returning something. SemanticMemory.query() can
                    # produce a result that is genuinely a full-text
                    # (EmbeddingStore's own fallback, or SomaticCompiler's
                    # keyword search) hit; each item already carries its
                    # own true retrieval_method (see semantic_memory.py),
                    # so only claim vector_used when at least one item is
                    # actually tagged "vector".
                    if any(i.retrieval_method == "vector" for i in vector_items):
                        report.vector_used = True
                        methods.append("vector")
                    elif "keyword" not in methods:
                        methods.append("keyword")
            except Exception as exc:
                logger.warning("[sittingface_retrieval] vector search failed: %s", exc)
                report.error = str(exc)

        if not report.vector_used and report.keyword_used:
            methods.append("keyword_fallback")

        report.items = self._finalize_items(items)
        report.methods_used = list(dict.fromkeys(methods))
        report.injected = bool(report.items)
        report.latency_ms = (time.perf_counter() - started) * 1000
        self._log_report(report)
        cache[key] = report
        return report

    def _keyword_pass(self, query: str) -> tuple[list[ExternalKnowledgeItem], list[str]]:
        compiler = self._get_compiler()
        methods: list[str] = []
        items: list[ExternalKnowledgeItem] = []
        if compiler is not None:
            try:
                keyword_items = self._keyword_retrieve(compiler, query)
                if keyword_items:
                    items.extend(keyword_items)
                    methods.append("keyword")
            except Exception as exc:
                logger.warning("[sittingface_retrieval] keyword search failed: %s", exc)
        return items, methods

    @staticmethod
    def _finalize_items(items: list[ExternalKnowledgeItem]) -> list[ExternalKnowledgeItem]:
        seen: set[str] = set()
        deduped: list[ExternalKnowledgeItem] = []
        for item in sorted(items, key=lambda i: i.relevance_score, reverse=True):
            sig = f"{item.source_chart}:{item.content[:120]}"
            if sig in seen:
                continue
            seen.add(sig)
            deduped.append(item)
        return deduped[:8]

    def _log_report(self, report: KnowledgeRetrievalReport) -> None:
        logger.info(
            "[sittingface_retrieval] query=%r attempted=%s methods=%s results=%d "
            "vector_available=%s vector_used=%s latency_ms=%.1f cache_hit=%s",
            report.query[:80],
            report.attempted,
            report.methods_used,
            len(report.items),
            report.vector_available,
            report.vector_used,
            report.latency_ms,
            report.cache_hit,
        )

    def _get_compiler(self) -> Any:
        try:
            from src.monkey_brain.kernel.plan.intents.intent_registry import get_somatic_compiler
            return get_somatic_compiler()
        except Exception:
            return None

    def _resolve_semantic_memory(self) -> Any:
        if self._semantic_memory is not None:
            return self._semantic_memory
        try:
            from src.monkey_brain.api.main import app
            cr = getattr(getattr(app, "state", None), "cognitive_runtime", None)
            return getattr(cr, "semantic_memory", None)
        except Exception:
            return None

    def _keyword_retrieve(self, compiler: Any, query: str) -> list[ExternalKnowledgeItem]:
        if not hasattr(compiler, "search"):
            return []
        hits = compiler.search(query) or []
        if not isinstance(hits, list):
            return []
        items: list[ExternalKnowledgeItem] = []
        charts_by_name = {c.name: c for c in getattr(compiler, "charts", [])}
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            name = str(hit.get("name") or "")
            chart = charts_by_name.get(name)
            content = _snippet_from_chart(chart, hit.get("matched_in") or [], query) if chart else name
            if not content:
                continue
            items.append(ExternalKnowledgeItem(
                content=content,
                source_chart=name,
                source_path=str(hit.get("source_path") or ""),
                retrieval_method="keyword",
                relevance_score=0.75,
                query=query,
                matched_fields=tuple(hit.get("matched_in") or ()),
            ))
        return items

    async def _vector_retrieve(
        self,
        semantic_memory: Any,
        query: str,
        *,
        existing_charts: set[str],
    ) -> list[ExternalKnowledgeItem]:
        """Despite the name, this does not itself guarantee a vector
        result: SemanticMemory.query() can return hits from EmbeddingStore
        (real cosineSimilarity, OR EmbeddingStore's own full-text fallback
        when no real embedding was available) and from SomaticCompiler's
        keyword chart search, merged into one list. Each hit already
        carries the actual mechanism that produced it (see
        semantic_memory.py); this only ever forwards that tag, never
        overrides it to "vector"."""
        result = await semantic_memory.query(query)
        if not isinstance(result, dict):
            return []
        raw_results = result.get("results") or []
        items: list[ExternalKnowledgeItem] = []
        for hit in raw_results:
            if not isinstance(hit, dict):
                continue
            text = str(hit.get("text") or hit.get("content") or "")
            if not text.strip():
                continue
            meta = hit.get("metadata") or {}
            chart_name = str(meta.get("name") or meta.get("chart") or "")
            if chart_name and chart_name in existing_charts:
                continue
            score = float(hit.get("score") or hit.get("_score") or 0.65)
            # Trust only the tag the actual source attached. A missing/
            # unrecognized tag is treated as "keyword" -- the
            # conservative direction, since over-claiming vector_used is
            # exactly the bug this is fixing; under-claiming it merely
            # loses a "vector" label on an otherwise-correct result.
            method = hit.get("retrieval_method")
            if method not in ("vector", "keyword"):
                method = "keyword"
            items.append(ExternalKnowledgeItem(
                content=text[:500],
                source_chart=chart_name,
                source_path=str(meta.get("source_path") or ""),
                retrieval_method=method,
                relevance_score=min(1.0, score),
                query=query,
            ))
        return items


def _copy_report(report: KnowledgeRetrievalReport, *, cache_hit: bool) -> KnowledgeRetrievalReport:
    return KnowledgeRetrievalReport(
        query=report.query,
        attempted=report.attempted,
        items=list(report.items),
        methods_used=list(report.methods_used),
        vector_available=report.vector_available,
        vector_used=report.vector_used,
        keyword_used=report.keyword_used,
        latency_ms=report.latency_ms,
        error=report.error,
        injected=report.injected,
        cache_hit=cache_hit,
    )


def _snippet_from_chart(chart: Any, matched_in: list[str], query: str) -> str:
    if chart is None:
        return ""
    values = getattr(chart, "values", {}) or {}
    parts: list[str] = [f"{getattr(chart, 'name', 'chart')} ({getattr(chart, 'chart_type', '')})"]

    for block_key in ("module", "capability", "agent"):
        block = values.get(block_key, {})
        if isinstance(block, dict):
            for field in ("description", "system_prompt", "role"):
                desc = block.get(field)
                if isinstance(desc, str) and desc.strip():
                    parts.append(desc.strip()[:400])
                    break
            if len(parts) > 1:
                break

    for inv in values.get("invariants", [])[:2]:
        if isinstance(inv, dict):
            stmt = inv.get("statement") or inv.get("rule")
            if stmt:
                parts.append(f"Invariant: {stmt}")

    for principle in values.get("principles", [])[:2]:
        if isinstance(principle, dict):
            stmt = principle.get("statement")
            if stmt:
                parts.append(f"Principle: {stmt}")

    for path in matched_in[:3]:
        if path in ("name", "chart_type"):
            continue
        snippet = _value_at_path(values, path)
        if snippet:
            parts.append(snippet[:200])

    return " | ".join(parts)[:600]


def _value_at_path(node: Any, path: str) -> str:
    if not path or not isinstance(node, dict):
        return ""
    cur: Any = node
    for part in path.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part)
    if isinstance(cur, str):
        return cur
    return ""


_DEFAULT_RETRIEVER: SittingFaceKnowledgeRetriever | None = None


def get_external_knowledge_retriever() -> SittingFaceKnowledgeRetriever:
    global _DEFAULT_RETRIEVER
    if _DEFAULT_RETRIEVER is None:
        _DEFAULT_RETRIEVER = SittingFaceKnowledgeRetriever()
    return _DEFAULT_RETRIEVER
