"""Vector backends for kernel/learn/memory/manager.py::MemoryManager's
vector_client — this codebase has no FAISS/Chroma/Pinecone dependency,
so cosine similarity is computed directly over real, explicit vectors.

InMemoryVectorBackend: pure in-process, no persistence — what tests and
(until now) the real runtime used. RedisBackedVectorBackend: same
in-memory index and search, but Redis-backed and reloaded at boot,
mirroring kernel/society/integration.py::_load_knowledge_graph's exact
pattern — the real runtime now uses this one so episodic memory
survives a restart the same way KnowledgeGraph entities already did.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

logger = logging.getLogger("agentos.memory.vector_backend")


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorBackend:
    """Process-local, thread-safe cosine-similarity index. One entry per
    id — upsert replaces in place (a fresh embedding for the same id is
    expected to supersede the old one, unlike TimelineStore's append-only
    history — this is a lookup index, not an audit trail)."""

    def __init__(self) -> None:
        self._vectors: dict[str, list[float]] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def upsert(self, id: str, vector: list[float], metadata: dict[str, Any]) -> None:
        with self._lock:
            self._vectors[id] = list(vector)
            self._metadata[id] = dict(metadata)

    def search(self, vector: list[float], top_k: int = 10) -> list[tuple[str, float, dict[str, Any]]]:
        """Returns (id, cosine_similarity, metadata), highest similarity first."""
        with self._lock:
            items = list(self._vectors.items())
        scored = [(id_, _cosine_similarity(vector, vec)) for id_, vec in items]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [(id_, score, dict(self._metadata.get(id_, {}))) for id_, score in scored[:top_k]]

    def remove(self, id: str) -> None:
        with self._lock:
            self._vectors.pop(id, None)
            self._metadata.pop(id, None)


class RedisBackedVectorBackend(InMemoryVectorBackend):
    """Same in-memory cosine-similarity index InMemoryVectorBackend
    already provides — search stays exactly as fast, no Redis round
    trip per query — but every upsert/remove now also writes through to
    a real Redis hash, and __init__ reloads it. Real gap this closes:
    an actor's whole episodic memory (experiences/conversations/prior-
    executions — see belief_runtime.py::_record_episodic_experience and
    api/routes/actors.py::ask_actor) was being silently wiped on every
    server restart, because the plain InMemoryVectorBackend this
    codebase used everywhere had nothing behind it — confirmed live
    (the Grounding Integrity panel correctly, honestly reported
    "queried, genuinely empty" for the first tick after a restart, even
    though real experiences existed moments earlier). Mirrors
    kernel/society/integration.py::_load_knowledge_graph's exact
    pattern — an in-memory index Redis-backs and reloads at boot — so
    episodic memory now survives a restart the same way KnowledgeGraph
    entities already did. Every Redis call try/except-degrades (same
    "never raise, keep the in-memory index authoritative" discipline
    TimelineStore/RunStore already established) rather than raising, so
    a Redis outage degrades to the prior in-memory-only behavior
    instead of breaking the tick pipeline."""

    _REDIS_KEY = "monkeybrain:memory:vectors"

    def __init__(self, redis_client: Any = None) -> None:
        super().__init__()
        self._redis = redis_client
        self._load()

    def _load(self) -> None:
        if self._redis is None:
            return
        try:
            raw = self._redis.hgetall(self._REDIS_KEY)
        except Exception:
            logger.warning("RedisBackedVectorBackend: load failed, starting empty", exc_info=True)
            return
        loaded = 0
        for id_, blob in raw.items():
            try:
                record = json.loads(blob)
                self._vectors[id_] = record["vector"]
                self._metadata[id_] = record["metadata"]
                loaded += 1
            except Exception:
                continue
        if loaded:
            logger.info("RedisBackedVectorBackend: loaded %d vectors from Redis", loaded)

    def upsert(self, id: str, vector: list[float], metadata: dict[str, Any]) -> None:
        super().upsert(id, vector, metadata)
        if self._redis is None:
            return
        try:
            self._redis.hset(
                self._REDIS_KEY,
                id,
                json.dumps({"vector": list(vector), "metadata": dict(metadata)}),
            )
        except Exception:
            logger.warning(
                "RedisBackedVectorBackend: write-through failed for %s",
                id,
                exc_info=True,
            )

    def remove(self, id: str) -> None:
        super().remove(id)
        if self._redis is None:
            return
        try:
            self._redis.hdel(self._REDIS_KEY, id)
        except Exception:
            logger.warning("RedisBackedVectorBackend: delete failed for %s", id, exc_info=True)
