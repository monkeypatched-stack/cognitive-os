"""SemanticMemory — embedding-based knowledge store using Elasticsearch.

All knowledge (text, images, videos, code, charts) is stored as
embeddings in Elasticsearch for similarity search and durable persistence.

Elasticsearch handles both vector search (kNN) and full-text search,
so we get similarity + keyword search in one system.

Architecture:
    Semantic Memory (SittingFace)  ←→  Procedural Memory (Graph + Q-table)
     │                                        │
     ├─ Elasticsearch (embeddings + text)     ├─ execution graph
     ├─ somatic charts (YAML)                 ├─ bellman Q-table
     ├─ knowledge items (multimodal)          ├─ experience replay buffer
     └─ similarity search                    └─ learning history
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger("agentos.semantic_memory")


class EmbeddingStore:
    """Elasticsearch-backed vector store for knowledge embeddings.

    Uses ES dense_vector for kNN similarity search + full-text for keywords.
    All knowledge is persisted durably — survives restarts.
    """

    INDEX = "semantic_memory"
    EMBEDDING_DIM = 768

    def __init__(self) -> None:
        self._client = None
        self._embedder = None
        self._items: list[dict] = []
        self._connected = False

    def initialize(self) -> bool:
        """Initialize Elasticsearch connection and create index."""
        es_url = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")

        try:
            import httpx
            resp = httpx.get(f"{es_url}/_cluster/health", timeout=3)
            if resp.status_code == 200:
                self._client = es_url
                self._connected = True
        except Exception as e:
            logger.debug("[embedding_store] ES connection failed: %s", e)
            self._connected = False
            return False

        try:
            import httpx as _httpx
            resp = _httpx.get(f"{es_url}/{self.INDEX}", timeout=3)
            if resp.status_code == 404:
                self._create_index(es_url)
        except Exception:
            logger.debug("initialize: suppressed exception", exc_info=True)

        # Check Ollama for embeddings
        try:
            import httpx as _httpx
            resp = _httpx.get("http://localhost:11434/api/tags", timeout=2)
            if resp.status_code == 200:
                self._embedder = "ollama"
        except Exception:
            self._embedder = None

        return self._connected

    def _create_index(self, es_url: str) -> None:
        """Create ES index with dense_vector mapping for similarity search.

        No "index"/"similarity" sub-params: those select the approximate,
        HNSW-indexed kNN feature added in Elasticsearch 8.0. This deployment
        runs ES 7.17 (confirmed live: GET / -> version.number 7.17.4), whose
        dense_vector mapping only ever supported a plain, non-indexed
        similarity vector, scored via script_score + cosineSimilarity — see
        search() below. Sending the 8.x params here is what silently
        produced today's mapping (ES 7.x rejects unknown dense_vector
        mapping params and _create_index swallows the error), which happened
        to still work for scripted scoring but would confuse anyone reading
        the mapping expecting real kNN indexing.
        """
        import httpx as _httpx
        mapping = {
            "mappings": {
                "properties": {
                    "text": {"type": "text"},
                    "embedding": {
                        "type": "dense_vector",
                        "dims": self.EMBEDDING_DIM,
                    },
                    "metadata": {"type": "object", "enabled": True},
                    "item_id": {"type": "keyword"},
                    "created_at": {"type": "date"},
                }
            }
        }
        try:
            _httpx.put(f"{es_url}/{self.INDEX}", json=mapping, timeout=5)
        except Exception as e:
            logger.debug("[embedding_store] index creation failed: %s", e)

    @property
    def available(self) -> bool:
        return self._connected

    async def embed(self, text: str) -> list[float] | None:
        """Embed text using Ollama nomic-embed-text, or None if Ollama is
        unavailable.

        No fabricated fallback vector: a hash-of-text is not a semantic
        embedding, and presenting one to cosineSimilarity as if it were
        would silently rank results by MD5-prefix similarity, not meaning.
        Callers must treat None as "no vector retrieval possible right
        now" and fall back to keyword/full-text search — never invent a
        vector to keep the vector code path superficially alive.
        """
        if self._embedder == "ollama":
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        "http://localhost:11434/api/embeddings",
                        json={"model": "nomic-embed-text:latest", "prompt": text},
                    )
                    embedding = resp.json().get("embedding")
                    if isinstance(embedding, list) and embedding:
                        return embedding
            except Exception as e:
                logger.debug("[embedding_store] embed failed: %s", e)

        return None

    async def add(self, item_id: str, text: str, metadata: dict | None = None) -> None:
        """Add knowledge item to Elasticsearch. Indexed with a real
        embedding when one is available; otherwise stored without one
        (still discoverable via _fallback_search's full-text match, never
        via a fabricated vector)."""
        if not self.available:
            return

        embedding = await self.embed(text)
        doc: dict[str, Any] = {
            "item_id": item_id,
            "text": text,
            "metadata": metadata or {},
            "created_at": int(time.time() * 1000),
        }
        if embedding is not None:
            doc["embedding"] = embedding
        self._items.append(doc)

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                await client.put(
                    f"{self._client}/{self.INDEX}/_doc/{item_id}",
                    json=doc,
                )
        except Exception as e:
            logger.debug("[embedding_store] ES index failed: %s", e)

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """Search by embedding similarity, falling back to full-text.

        Uses script_score + cosineSimilarity, not the top-level `knn` search
        clause: that clause is an ES 8.0+ feature and this deployment runs
        7.17 (confirmed live) — every `knn` query got HTTP 400 back, which
        the previous version never checked for. resp.json() happily parses
        an error body too, so `data.get("hits", {}).get("hits", [])` read
        {} from the error response and returned [] with NO exception raised
        — the intended `except -> _fallback_search` path was silently never
        reached. Similarity search returned nothing, always, on this ES
        version, and nobody could tell from the code that it was broken.

        Every returned hit carries retrieval_method ("vector" for a real
        cosineSimilarity match, "keyword" for the full-text fallback) so a
        caller merging results from multiple sources (SemanticMemory.query)
        never has to guess, or assume, which one actually produced a hit.
        """
        if not self.available:
            return []

        embedding = await self.embed(query)
        if embedding is None:
            # No real embedding available -- do not fabricate one and
            # send it to cosineSimilarity. Degrade straight to full-text.
            return await self._fallback_search(query, limit)

        try:
            import httpx
            similarity_query = {
                "size": limit,
                "query": {
                    "script_score": {
                        "query": {"match_all": {}},
                        "script": {
                            "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                            "params": {"query_vector": embedding},
                        },
                    }
                },
                "_source": {"excludes": ["embedding"]},
            }

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._client}/{self.INDEX}/_search",
                    json=similarity_query,
                )
                if resp.status_code != 200:
                    logger.debug(
                        "[embedding_store] similarity search returned %d: %s",
                        resp.status_code, resp.text[:300],
                    )
                    return await self._fallback_search(query, limit)
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                return [{**h["_source"], "retrieval_method": "vector"} for h in hits]
        except Exception as e:
            logger.debug("[embedding_store] similarity search failed: %s", e)
            return await self._fallback_search(query, limit)

    async def _fallback_search(self, query: str, limit: int) -> list[dict]:
        """Full-text search fallback when embedding search is unavailable
        or fails. Never mislabeled as a vector result — see search()'s
        docstring."""
        try:
            import httpx
            match_query = {
                "size": limit,
                "query": {"match": {"text": query}},
                "_source": {"excludes": ["embedding"]},
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._client}/{self.INDEX}/_search",
                    json=match_query,
                )
                hits = resp.json().get("hits", {}).get("hits", [])
                return [{**h["_source"], "retrieval_method": "keyword"} for h in hits]
        except Exception:
            return []

    async def get_all(self) -> list[dict]:
        """Return all stored knowledge items."""
        return list(self._items)


class ExperienceReplayBuffer:
    """Fixed-size buffer for experience replay in Q-learning.

    Stores (state, action, reward, next_state) tuples.
    Samples random mini-batches for off-policy learning.
    """

    def __init__(self, capacity: int = 1000) -> None:
        self._capacity = capacity
        self._buffer: list[dict] = []
        self._position = 0

    def add(self, experience: dict) -> None:
        if len(self._buffer) < self._capacity:
            self._buffer.append(experience)
        else:
            self._buffer[self._position] = experience
        self._position = (self._position + 1) % self._capacity

    def sample(self, batch_size: int = 32) -> list[dict]:
        import random
        return random.sample(self._buffer, min(batch_size, len(self._buffer)))

    def __len__(self) -> int:
        return len(self._buffer)


class RateLimiter:
    """Per-agent rate limiter for concurrent execution."""

    def __init__(self, max_concurrent: int = 4, per_agent_limit: int = 2) -> None:
        self._max_concurrent = max_concurrent
        self._per_agent_limit = per_agent_limit
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._agent_semaphores: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, agent_name: str) -> None:
        await self._semaphore.acquire()
        async with self._lock:
            if agent_name not in self._agent_semaphores:
                self._agent_semaphores[agent_name] = asyncio.Semaphore(self._per_agent_limit)
        await self._agent_semaphores[agent_name].acquire()

    async def release(self, agent_name: str) -> None:
        self._semaphore.release()
        async with self._lock:
            if agent_name in self._agent_semaphores:
                self._agent_semaphores[agent_name].release()


class SemanticMemory:
    """Embedding-based semantic memory using Elasticsearch.

    All knowledge stored as embeddings for similarity search.
    Durable: survives restarts via ES persistence.
    """

    def __init__(self) -> None:
        self._compiler: Any = None
        self._embeddings: EmbeddingStore = EmbeddingStore()
        self._experience_buffer: ExperienceReplayBuffer = ExperienceReplayBuffer()
        self._rate_limiter: RateLimiter = RateLimiter()
        self._initialized: bool = False

    async def initialize(self) -> None:
        """Initialize semantic memory: load charts, set up ES, restore from DB."""
        try:
            from src.monkey_brain.kernel.plan.intents.intent_registry import get_somatic_compiler
            self._compiler = get_somatic_compiler()
        except Exception:
            logger.debug("initialize: suppressed exception", exc_info=True)

        self._embeddings.initialize()

        if self._compiler:
            await self._index_charts()

        self._initialized = True
        logger.info("[semantic_memory] Initialized (compiler=%s, es=%s)",
                     self._compiler is not None, self._embeddings.available)

    async def _index_charts(self) -> None:
        """Index somatic chart knowledge as embeddings.

        Was calling asyncio.get_event_loop().run_until_complete() to bridge
        into async self._embeddings.add() from this (previously) sync
        method -- fine as long as self._compiler.charts was always empty
        (nothing to iterate, so the call was never reached). Once the
        SittingFace bootstrap path-check bug was fixed and charts actually
        loaded (93 real charts), this ran for the first time and crashed
        boot outright: run_until_complete() cannot run inside a loop that's
        already running (this method's only caller, initialize(), is
        itself always invoked from within the boot sequence's own running
        event loop). Made properly async instead of bridging.
        """
        if not self._compiler or not hasattr(self._compiler, "charts"):
            return
        for chart in self._compiler.charts:
            text = self._chart_to_text(chart)
            if text:
                await self._embeddings.add(f"chart:{chart.name}", text, {"type": "chart", "name": chart.name})
            for inv in chart.values.get("invariants", []):
                inv_text = f"Invariant: {inv.get('rule', '')} - {inv.get('statement', '')}"
                await self._embeddings.add(f"invariant:{chart.name}:{inv.get('rule', '')}", inv_text, {"type": "invariant"})

    def _chart_to_text(self, chart: Any) -> str:
        parts = [f"Chart: {chart.name}"]
        for inv in chart.values.get("invariants", []):
            parts.append(f"Rule: {inv.get('rule', '')} - {inv.get('statement', '')}")
        for p in chart.values.get("principles", []):
            parts.append(f"Principle: {p.get('statement', '')}")
        return " | ".join(parts)

    @property
    def available(self) -> bool:
        return self._initialized

    async def query(self, question: str) -> dict | None:
        """Merges two genuinely different retrieval mechanisms into one
        `results` list -- embedding-similarity hits from EmbeddingStore
        (already self-tagged retrieval_method by search(), whether that
        turned out to be a real vector match or its own full-text
        fallback) and SomaticCompiler's own keyword chart search. Every
        item here MUST carry an accurate retrieval_method: a caller (see
        sittingface_retrieval.py::_vector_retrieve) has no way to tell
        the two sources apart otherwise, and must never assume every item
        returned from this method is a vector-similarity result merely
        because query() was called."""
        if not self.available:
            return None
        results = []
        if self._embeddings.available:
            try:
                embedding_results = await self._embeddings.search(question, limit=10)
                results.extend(embedding_results)
            except Exception:
                logger.debug("query: suppressed exception", exc_info=True)
        if self._compiler and hasattr(self._compiler, "search"):
            try:
                chart_results = self._compiler.search(question)
                if isinstance(chart_results, list):
                    results.extend({**r, "retrieval_method": "keyword"} for r in chart_results if isinstance(r, dict))
            except Exception:
                logger.debug("query: suppressed exception", exc_info=True)
        return {"results": results, "count": len(results), "source": "semantic_memory"}

    async def store(self, item_id: str, text: str, metadata: dict | None = None) -> None:
        if self._embeddings.available:
            await self._embeddings.add(item_id, text, metadata)

    def record_experience(self, state: str, action: str, reward: float, next_state: str) -> None:
        """Record experience for replay buffer."""
        self._experience_buffer.add({
            "state": state, "action": action, "reward": reward,
            "next_state": next_state, "timestamp": time.time(),
        })

    def sample_experiences(self, batch_size: int = 32) -> list[dict]:
        return self._experience_buffer.sample(batch_size)

    def summary(self) -> dict:
        chart_count = 0
        invariant_count = 0
        principle_count = 0
        if self._compiler:
            s = self._compiler.summary() if hasattr(self._compiler, "summary") else {}
            chart_count = s.get("total_charts", len(s.get("chart_names", [])))
            if hasattr(self._compiler, "charts"):
                for chart in self._compiler.charts:
                    invariant_count += len(chart.values.get("invariants", []))
                    principle_count += len(chart.values.get("principles", []))
        return {
            "initialized": self._initialized,
            "charts": chart_count,
            "invariants": invariant_count,
            "principles": principle_count,
            "es_connected": self._embeddings.available,
            "experience_buffer": len(self._experience_buffer),
        }
