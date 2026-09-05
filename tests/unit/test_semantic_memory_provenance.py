"""Semantic retrieval provenance and fail-safe-degradation tests
(kernel/semantic_memory.py).

Two invariants under test:

1. Every result EmbeddingStore returns carries the retrieval mechanism
   that actually produced it ("vector" for a real cosineSimilarity hit,
   "keyword" for its own full-text fallback) -- a caller merging results
   from multiple sources must never have to guess, or assume "vector"
   merely because the call went through this store.
2. When the real embedding provider (Ollama) is unavailable, embed()
   returns None rather than a fabricated hash-based vector -- no
   meaningless vector is ever sent to Elasticsearch's cosineSimilarity,
   and search()/add() degrade to genuine full-text behavior instead.
"""
from __future__ import annotations

import pytest

from src.monkey_brain.kernel.semantic_memory import EmbeddingStore, SemanticMemory


class _FakeResponse:
    def __init__(self, status_code: int = 200, body: object | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text

    def json(self) -> object:
        return self._body


class _FakeAsyncClient:
    """Minimal async-context-manager stand-in for httpx.AsyncClient,
    matching this repo's established convention (see
    tests/security/test_opa_fail_closed.py). Each real EmbeddingStore
    call opens its OWN `async with httpx.AsyncClient() as client:` block
    (embed() and search() are separate calls) -- the queue is shared
    across every instance the factory produces, in call order, rather
    than reset per instantiation."""

    def __init__(self, *, queue: list[object], **_kw) -> None:
        self._queue = queue

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def _next(self):
        if not self._queue:
            return _FakeResponse(200, {})
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def post(self, url: str, json: dict | None = None) -> _FakeResponse:
        return await self._next()

    async def put(self, url: str, json: dict | None = None) -> _FakeResponse:
        return await self._next()


def _patch_httpx(monkeypatch, responses: list[object]) -> None:
    import httpx

    queue = list(responses)

    def _factory(*args, **kwargs):
        return _FakeAsyncClient(queue=queue)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


def _connected_store(*, embedder: str | None) -> EmbeddingStore:
    store = EmbeddingStore()
    store._client = "http://fake-es:9200"
    store._connected = True
    store._embedder = embedder
    return store


class TestEmbedFailsSafeNotFake:
    @pytest.mark.asyncio
    async def test_ollama_unreachable_returns_none_not_a_hash_vector(self, monkeypatch):
        store = _connected_store(embedder="ollama")
        _patch_httpx(monkeypatch, [ConnectionError("ollama down")])

        result = await store.embed("some knowledge text")

        assert result is None

    @pytest.mark.asyncio
    async def test_no_embedder_configured_returns_none(self, monkeypatch):
        store = _connected_store(embedder=None)

        result = await store.embed("some knowledge text")

        assert result is None

    @pytest.mark.asyncio
    async def test_real_ollama_response_returns_the_actual_vector(self, monkeypatch):
        store = _connected_store(embedder="ollama")
        real_vector = [0.1, 0.2, 0.3]
        _patch_httpx(monkeypatch, [_FakeResponse(200, {"embedding": real_vector})])

        result = await store.embed("some knowledge text")

        assert result == real_vector

    @pytest.mark.asyncio
    async def test_malformed_ollama_response_returns_none_not_padding(self, monkeypatch):
        """A response with no usable "embedding" key must not silently
        become a zero-padded placeholder masquerading as a real vector."""
        store = _connected_store(embedder="ollama")
        _patch_httpx(monkeypatch, [_FakeResponse(200, {"unexpected": "shape"})])

        result = await store.embed("some knowledge text")

        assert result is None


class TestSearchProvenance:
    @pytest.mark.asyncio
    async def test_real_similarity_hit_is_tagged_vector(self, monkeypatch):
        store = _connected_store(embedder="ollama")
        _patch_httpx(monkeypatch, [
            _FakeResponse(200, {"embedding": [0.1, 0.2]}),  # embed()
            _FakeResponse(200, {"hits": {"hits": [
                {"_source": {"text": "real similarity hit", "item_id": "a"}},
            ]}}),  # similarity search
        ])

        results = await store.search("query")

        assert len(results) == 1
        assert results[0]["retrieval_method"] == "vector"

    @pytest.mark.asyncio
    async def test_no_embedder_degrades_straight_to_fallback_tagged_keyword(self, monkeypatch):
        """No real embedding available -> never even attempt a
        cosineSimilarity call with a fabricated vector; go straight to
        full-text and tag it accurately."""
        store = _connected_store(embedder=None)
        _patch_httpx(monkeypatch, [
            _FakeResponse(200, {"hits": {"hits": [
                {"_source": {"text": "full text hit", "item_id": "b"}},
            ]}}),  # _fallback_search's match query -- the ONLY network call
        ])

        results = await store.search("query")

        assert len(results) == 1
        assert results[0]["retrieval_method"] == "keyword"

    @pytest.mark.asyncio
    async def test_similarity_query_error_falls_back_and_is_tagged_keyword(self, monkeypatch):
        """A real embedding existed, but the similarity query itself
        failed (e.g. ES error) -- the fallback path that runs afterward
        is still full-text, and must still be tagged accurately, not
        "vector" merely because a real embedding was computed first."""
        store = _connected_store(embedder="ollama")
        _patch_httpx(monkeypatch, [
            _FakeResponse(200, {"embedding": [0.1, 0.2]}),  # embed()
            _FakeResponse(500, {}, text="internal error"),  # similarity query fails
            _FakeResponse(200, {"hits": {"hits": [
                {"_source": {"text": "fallback hit", "item_id": "c"}},
            ]}}),  # _fallback_search
        ])

        results = await store.search("query")

        assert len(results) == 1
        assert results[0]["retrieval_method"] == "keyword"

    @pytest.mark.asyncio
    async def test_unavailable_store_returns_empty_not_an_error(self):
        store = EmbeddingStore()  # never initialized/connected
        assert await store.search("query") == []


class TestAddNeverFabricatesEmbedding:
    @pytest.mark.asyncio
    async def test_add_without_embedder_indexes_without_embedding_key(self, monkeypatch):
        store = _connected_store(embedder=None)
        _patch_httpx(monkeypatch, [_FakeResponse(200, {})])  # the ES PUT

        await store.add("item-1", "some text", {"type": "chart"})

        doc = store._items[-1]
        assert "embedding" not in doc
        assert doc["text"] == "some text"

    @pytest.mark.asyncio
    async def test_add_with_embedder_indexes_the_real_vector(self, monkeypatch):
        store = _connected_store(embedder="ollama")
        real_vector = [0.5, 0.5]
        _patch_httpx(monkeypatch, [
            _FakeResponse(200, {"embedding": real_vector}),  # embed()
            _FakeResponse(200, {}),  # the ES PUT
        ])

        await store.add("item-2", "some text", {"type": "chart"})

        doc = store._items[-1]
        assert doc["embedding"] == real_vector


class TestSemanticMemoryQueryProvenance:
    @pytest.mark.asyncio
    async def test_compiler_hits_are_tagged_keyword_not_vector(self):
        """SemanticMemory.query() merges EmbeddingStore results with
        SomaticCompiler.search() results into one list -- the compiler
        branch must never be silently presented as a vector result."""
        memory = SemanticMemory()
        memory._initialized = True

        class _EmptyEmbeddings:
            available = False

        class _FakeCompiler:
            def search(self, question):
                return [{"name": "some-chart", "chart_type": "capability", "source_path": "/x"}]

        memory._embeddings = _EmptyEmbeddings()
        memory._compiler = _FakeCompiler()

        result = await memory.query("some question")

        assert result is not None
        assert len(result["results"]) == 1
        assert result["results"][0]["retrieval_method"] == "keyword"
