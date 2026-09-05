"""CachedSittingFaceRetriever (kernel/edge/semantic_cache.py) -- proves
the cache never re-labels or fabricates retrieval provenance, and that a
repeated query with the same knowledge_version is served without a
second real retrieval."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from src.monkey_brain.kernel.edge.semantic_cache import CachedSittingFaceRetriever
from src.monkey_brain.kernel.knowledge.external_context import ExternalKnowledgeItem, KnowledgeRetrievalReport


class _FakeRetriever:
    def __init__(self) -> None:
        self.calls = 0

    def retrieve_sync(self, query: str, *, cycle_id: str = "", force: bool = False) -> KnowledgeRetrievalReport:
        self.calls += 1
        return KnowledgeRetrievalReport(
            query=query, attempted=True, keyword_used=True, vector_used=False,
            methods_used=["keyword"],
            items=[ExternalKnowledgeItem(content="fact", source_chart="c", retrieval_method="keyword")],
        )

    async def retrieve(self, query: str, *, cycle_id: str = "", force: bool = False, meta: dict | None = None) -> KnowledgeRetrievalReport:
        self.calls += 1
        return KnowledgeRetrievalReport(
            query=query, attempted=True, keyword_used=False, vector_used=True,
            methods_used=["vector"],
            items=[ExternalKnowledgeItem(content="vector fact", source_chart="c", retrieval_method="vector")],
        )


class TestCacheAvoidsRedundantRetrieval:
    def test_second_sync_call_with_same_key_does_not_call_the_real_retriever(self):
        fake = _FakeRetriever()
        cached = CachedSittingFaceRetriever(fake)
        cached.retrieve_sync("what is CAPA", cycle_id="c1", knowledge_version="k1")
        cached.retrieve_sync("what is CAPA", cycle_id="c1", knowledge_version="k1")
        assert fake.calls == 1

    @pytest.mark.asyncio
    async def test_second_async_call_with_same_key_does_not_call_the_real_retriever(self):
        fake = _FakeRetriever()
        cached = CachedSittingFaceRetriever(fake)
        await cached.retrieve("what is CAPA", cycle_id="c1", knowledge_version="k1")
        await cached.retrieve("what is CAPA", cycle_id="c1", knowledge_version="k1")
        assert fake.calls == 1

    def test_query_normalization_still_hits_the_cache(self):
        fake = _FakeRetriever()
        cached = CachedSittingFaceRetriever(fake)
        cached.retrieve_sync("What Is  CAPA", cycle_id="c1", knowledge_version="k1")
        cached.retrieve_sync("what is capa", cycle_id="c1", knowledge_version="k1")
        assert fake.calls == 1

    def test_knowledge_version_change_forces_real_retrieval(self):
        fake = _FakeRetriever()
        cached = CachedSittingFaceRetriever(fake)
        cached.retrieve_sync("what is CAPA", cycle_id="c1", knowledge_version="k1")
        cached.retrieve_sync("what is CAPA", cycle_id="c1", knowledge_version="k2")
        assert fake.calls == 2

    def test_force_bypasses_the_cache(self):
        fake = _FakeRetriever()
        cached = CachedSittingFaceRetriever(fake)
        cached.retrieve_sync("what is CAPA", cycle_id="c1", knowledge_version="k1")
        cached.retrieve_sync("what is CAPA", cycle_id="c1", force=True, knowledge_version="k1")
        assert fake.calls == 2


class TestProvenanceIsNeverRewrittenByTheCache:
    def test_cached_report_keeps_the_original_retrieval_method(self):
        fake = _FakeRetriever()
        cached = CachedSittingFaceRetriever(fake)
        first = cached.retrieve_sync("what is CAPA", cycle_id="c1", knowledge_version="k1")
        second = cached.retrieve_sync("what is CAPA", cycle_id="c1", knowledge_version="k1")

        assert first.items[0].retrieval_method == "keyword"
        assert second.items[0].retrieval_method == "keyword"
        assert second.vector_used == first.vector_used
        assert second.methods_used == first.methods_used

    def test_cache_hit_is_flagged_true_only_on_the_reused_result(self):
        fake = _FakeRetriever()
        cached = CachedSittingFaceRetriever(fake)
        first = cached.retrieve_sync("what is CAPA", cycle_id="c1", knowledge_version="k1")
        second = cached.retrieve_sync("what is CAPA", cycle_id="c1", knowledge_version="k1")
        assert first.cache_hit is False
        assert second.cache_hit is True

    @pytest.mark.asyncio
    async def test_async_cached_report_keeps_vector_provenance(self):
        fake = _FakeRetriever()
        cached = CachedSittingFaceRetriever(fake)
        first = await cached.retrieve("what is CAPA", cycle_id="c1", knowledge_version="k1")
        second = await cached.retrieve("what is CAPA", cycle_id="c1", knowledge_version="k1")
        assert first.items[0].retrieval_method == "vector"
        assert second.items[0].retrieval_method == "vector"
        assert second.vector_used is True


class TestUnattemptedRetrievalIsNeverCached:
    def test_a_report_that_was_never_attempted_is_not_cached(self):
        class _SkippingRetriever:
            def __init__(self):
                self.calls = 0

            def retrieve_sync(self, query, *, cycle_id="", force=False):
                self.calls += 1
                return KnowledgeRetrievalReport(query=query, attempted=False)

        fake = _SkippingRetriever()
        cached = CachedSittingFaceRetriever(fake)
        cached.retrieve_sync("buy milk", cycle_id="c1", knowledge_version="k1")
        cached.retrieve_sync("buy milk", cycle_id="c1", knowledge_version="k1")
        assert fake.calls == 2
