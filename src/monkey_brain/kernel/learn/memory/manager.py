"""MemoryManager — allocate working context and persist to episodic stream.

Context-Aware Personalized Planning refactor: this class previously only
knew how to WRITE (allocate_working_context/persist_to_episodic_stream) —
record_experience()/search_episodic() below are the first read/retrieval
path this scaffold has ever had, needed so ContextConstructionEngine
(kernel/pipeline/planning/context_engine.py) can retrieve an actor's past
experiences/conversations/executions by semantic similarity.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.learn.memory.primitives import MemoryNode, ProvenanceToken

_embedding_registry: Any = None


def _get_embedding_registry() -> Any:
    """Process-wide cached EmbeddingRegistry — SBERTEmbedder lazy-loads a
    real model on first use; rebuilding the registry per call (the
    original bug here) reloaded the model on every single record_experience/
    search_episodic call instead of once per process."""
    global _embedding_registry
    if _embedding_registry is None:
        from src.monkey_brain.kernel.plan.embedding.registry import (
            build_default_registry,
        )

        _embedding_registry = build_default_registry()
    return _embedding_registry


class MemoryManager:
    """Manages the full memory lifecycle for a cognitive runtime thread.

    Responsibilities:
    - Allocate bounded working-memory slots per task
    - Evict working memory and persist it as episodic traces
    - Delegate physical storage to pluggable vector/graph backends

    The manager never enforces retention policy — that is CognitiveGC's job.
    It never compiles patterns — that is BackgroundCompactor's job.
    """

    # Default staleness threshold: 30 days.  CognitiveGC uses this to decide
    # which episodic nodes are candidates for tombstoning.
    staleness_threshold_seconds: float = 30 * 24 * 60 * 60

    def __init__(self, vector_client: Any, graph_client: Any) -> None:
        self.vector_db = vector_client
        self.graph_db = graph_client

        # Volatile working space — one slot per active task, keyed by
        # (actor_id, task_id) so two actors' tasks can never collide on the
        # same task_id (Actor Cell Architecture planning found this keyed
        # by task_id alone, with no actor_id at all — docs/
        # ACTOR_CELL_ARCHITECTURE.md).
        self.working_memory: dict[tuple[str, str], MemoryNode] = {}

        # Cognitive State refactor: a materialized per-actor index over
        # record_experience()'s real writes — the only way to answer
        # "what has this actor experienced" without either a query string
        # (search_episodic requires one, plus a blocking embed call per
        # lookup) or learning the graph_db's own query language. Not a
        # second source of truth: every entry here is the SAME MemoryNode
        # already written to vector_db/graph_db by record_experience(),
        # just also kept here for cheap, query-free retrieval.
        self._by_actor: dict[str, list[MemoryNode]] = {}

    def allocate_working_context(
        self,
        actor_id: str,
        task_id: str,
        initial_state: dict[str, Any],
        provenance: ProvenanceToken,
    ) -> MemoryNode:
        """Allocate a volatile workspace for an active runtime thread,
        scoped to actor_id so a task_id collision across actors can never
        share a working-memory slot."""
        node = MemoryNode(
            node_id=task_id,
            memory_type="working",
            payload=initial_state,
            provenance=provenance,
        )
        self.working_memory[(actor_id, task_id)] = node
        return node

    def persist_to_episodic_stream(self, actor_id: str, task_id: str) -> MemoryNode | None:
        """Evict working memory and commit it to the long-term episodic store.

        Returns the persisted node, or None if (actor_id, task_id) was not
        in working memory. The node's memory_type is promoted to 'episodic'
        before writing.
        """
        node = self.working_memory.pop((actor_id, task_id), None)
        if not node:
            return None

        node.memory_type = "episodic"
        node.last_accessed = time.time()

        self.graph_db.insert_node(node.node_id, node.payload, label="EpisodicTrace")
        if node.vector_embedding:
            self.vector_db.upsert(
                id=node.node_id,
                vector=node.vector_embedding,
                metadata=node.payload,
            )

        return node

    def record_experience(
        self,
        actor_id: str,
        kind: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        provenance: ProvenanceToken | None = None,
    ) -> MemoryNode:
        """One-shot write straight to the episodic store — for recording a
        COMPLETED experience/conversation/execution (not a live task's
        working memory, which still goes through allocate_working_context/
        persist_to_episodic_stream). Embeds `text` via kernel/plan/embedding's
        EmbeddingRegistry (document modality — SBERT with a BOW/hash
        fallback chain, no hard ML dependency)."""
        embedding = _get_embedding_registry().embed(text)

        payload = {"actor_id": actor_id, "kind": kind, "text": text, **(metadata or {})}
        node = MemoryNode(
            node_id=uuid4().hex,
            memory_type="episodic",
            payload=payload,
            vector_embedding=embedding.vector.tolist(),
            provenance=provenance,
        )
        # `name` is content-derived (unlike `label`, which stays the
        # constant "EpisodicTrace" structural marker cognitive_gc.py /
        # compactor.py match on) so KnowledgeGraph.entities_by_keywords
        # can actually find this experience from another actor's goal
        # keywords, instead of every recorded experience sharing one
        # indistinguishable name.
        self.graph_db.insert_node(
            node.node_id,
            node.payload,
            label="EpisodicTrace",
            name=f"{kind}: {text}",
        )
        self.vector_db.upsert(id=node.node_id, vector=node.vector_embedding, metadata=payload)
        self._by_actor.setdefault(actor_id, []).append(node)
        return node

    def recent_for_actor(self, actor_id: str, limit: int = 50) -> list[MemoryNode]:
        """This actor's own recorded experiences, newest first — no query
        string needed (unlike search_episodic) and no embedding-model
        round trip, since this reads the materialized _by_actor index
        record_experience() already maintains."""
        nodes = self._by_actor.get(actor_id, [])
        return list(reversed(nodes[-limit:]))

    def search_episodic(self, query_text: str, top_k: int = 10, actor_id: str | None = None) -> list[MemoryNode]:
        """Semantic search over recorded experiences — the retrieval
        capability this scaffold never had. Embeds query_text with the
        same registry record_experience() uses, so query and stored
        vectors live in the same embedding space."""
        query_vector = _get_embedding_registry().embed(query_text).vector.tolist()

        results = self.vector_db.search(query_vector, top_k=top_k * 3 if actor_id else top_k)
        nodes = []
        for node_id, score, metadata in results:
            if actor_id is not None and metadata.get("actor_id") != actor_id:
                continue
            payload = {**metadata, "_retrieval_score": score}
            nodes.append(
                MemoryNode(
                    node_id=node_id,
                    memory_type="episodic",
                    payload=payload,
                    vector_embedding=None,
                    access_count=0,
                )
            )
            if len(nodes) >= top_k:
                break
        return nodes
