"""Moss (docs.usemoss.dev / PyPI `moss`) as an OPTIONAL semantic-retrieval
backend for the edge — narrowed scope, see docs/CLOUD_EDGE_ACTOR_ARCHITECTURE.md
Section 18's "MossDB scope decision" note for the full reasoning.

Moss was originally proposed as the general edge persistence substrate
(replacing kernel/edge/local_store.py's SQLite backend for policy/
delegation/execution/idempotency/world-state). It is not that kind of
system: MossClient is a cloud-backed semantic-search SaaS
(project_id/project_key credentials, `service.usemoss.dev`), document/
embedding-shaped, with no documented transaction or atomicity guarantees.
Building "atomic security state" (Section 6 of that task) on top of it
would mean either fabricating an atomicity guarantee it doesn't provide,
or silently weakening kernel/edge/local_store.py's actual atomic writes —
both rejected. kernel/edge/local_store.py is UNCHANGED and remains the
production backend for everything except retrieval.

What Moss genuinely fits: kernel/knowledge/sittingface_retrieval.py's own
`semantic_memory` dependency already has exactly the shape Moss can
satisfy --

    async def query(self, query: str) -> dict:
        return {"results": [{"text": ..., "metadata": {...},
                              "score": ..., "retrieval_method": "vector"}]}

-- the SAME contract kernel/semantic_memory.py::SemanticMemory.query()
already implements against Elasticsearch+Ollama. MossSemanticMemory below
is a drop-in ALTERNATIVE satisfying that identical contract; it requires
zero changes to sittingface_retrieval.py, and is never on the default
path (build_moss_semantic_memory() returns None unless MOSS_PROJECT_ID/
MOSS_PROJECT_KEY are configured).

Honest limitation: this module implements the QUERY side of the contract
against a real Moss session/index. It does not implement a pipeline that
populates a Moss index from CognitiveOS's own knowledge charts -- no such
pipeline exists yet. Constructing this class does not, by itself, give
the edge any actual indexed knowledge to retrieve; a caller must populate
the session (via `MossSemanticMemory.index_documents()`) before `query()`
can return anything.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Protocol

logger = logging.getLogger("agentos.edge.moss_retrieval")

DEFAULT_INDEX_NAME = "cognitiveos-edge-knowledge"


class MossUnavailableError(RuntimeError):
    """Raised only by build_moss_semantic_memory() when a caller
    explicitly requires Moss and it is not configured -- MossSemanticMemory
    itself is constructed directly by tests with an injected fake client
    and never raises this on its own."""


class _MossSessionProtocol(Protocol):
    async def add_docs(self, docs: list[Any], options: Any = None) -> Any: ...
    async def query(self, query: str, options: Any = None) -> Any: ...


class _MossClientProtocol(Protocol):
    async def session(self, index_name: str, model_id: str | None = None) -> _MossSessionProtocol: ...


class MossSemanticMemory:
    """Satisfies the exact `semantic_memory.query(query) -> dict` contract
    kernel/knowledge/sittingface_retrieval.py::SittingFaceKnowledgeRetriever
    already depends on (see that module's `_vector_retrieve`) -- a drop-in
    alternative to kernel/semantic_memory.py::SemanticMemory, injected via
    `SittingFaceKnowledgeRetriever(semantic_memory=...)` /
    `.set_semantic_memory(...)`, never a modification to that class.

    `client` is dependency-injected (a real MossClient in production, a
    fake satisfying _MossClientProtocol in tests) -- this class never
    constructs its own MossClient, so it never needs real credentials to
    be unit-tested.
    """

    def __init__(self, client: _MossClientProtocol, *, index_name: str = DEFAULT_INDEX_NAME, top_k: int = 5) -> None:
        self._client = client
        self._index_name = index_name
        self._top_k = top_k
        self._session: _MossSessionProtocol | None = None

    @property
    def available(self) -> bool:
        """SittingFaceKnowledgeRetriever._retrieve_core_async() gates
        vector retrieval on `getattr(semantic_memory, "available", False)`
        -- matching kernel/semantic_memory.py::EmbeddingStore.available's
        own convention (a coarse "is there a backend to try" flag, not a
        live round trip): True once a client is injected, since
        MossClient's own __init__ performs no network I/O and cannot
        fail eagerly. Real failures surface from query() itself, which
        already degrades to empty results rather than raising."""
        return True

    async def _get_session(self) -> _MossSessionProtocol:
        if self._session is None:
            self._session = await self._client.session(self._index_name)
        return self._session

    async def index_documents(self, documents: list[dict[str, Any]]) -> int:
        """documents: [{"id": str, "text": str, "metadata": dict}, ...].
        Real, but deliberately the ONLY population path this module
        provides -- nothing here sources documents from Neo4j/knowledge
        charts automatically; a caller decides what to index."""
        from moss import DocumentInfo

        session = await self._get_session()
        docs = [
            DocumentInfo(id=d["id"], text=d["text"], metadata=d.get("metadata") or {})
            for d in documents
        ]
        added, _updated = await session.add_docs(docs)
        return added

    async def query(self, query: str) -> dict[str, Any]:
        """Matches SemanticMemory.query()'s exact return shape. Fails
        soft (empty results, never an exception) on any Moss error --
        auth failure, network error, no session/index yet -- exactly the
        same "degrade to no external knowledge" convention
        kernel/semantic_memory.py already follows, logged for
        observability but never propagated up into
        SittingFaceKnowledgeRetriever, which has no Moss-specific error
        handling of its own (nor should it)."""
        try:
            session = await self._get_session()
            result = await session.query(query, self._query_options())
        except Exception:
            logger.warning("MossSemanticMemory.query: Moss call failed, degrading to no results", exc_info=True)
            return {"results": []}

        results = []
        for doc in getattr(result, "docs", None) or []:
            text = getattr(doc, "text", "") or ""
            if not text.strip():
                continue
            results.append({
                "text": text,
                "metadata": dict(getattr(doc, "metadata", None) or {}),
                "score": float(getattr(doc, "score", 0.0) or 0.0),
                # Moss is purely embedding/vector-based semantic search --
                # this is the one place in this module a retrieval_method
                # tag is asserted rather than forwarded, and it is honest:
                # there is no keyword-only code path in Moss's query().
                "retrieval_method": "vector",
            })
        return {"results": results}

    def _query_options(self) -> Any:
        from moss import QueryOptions
        return QueryOptions(top_k=self._top_k)


def build_moss_semantic_memory(*, index_name: str = DEFAULT_INDEX_NAME, require: bool = False) -> MossSemanticMemory | None:
    """Clear, explicit startup behavior, matching
    kernel/edge/ros_integration.py::build_ros_execution_adapter's own
    convention:

    - require=False (default): returns None if MOSS_PROJECT_ID/
      MOSS_PROJECT_KEY are not set in the environment -- the normal
      CognitiveOS runtime never depends on Moss and never crashes because
      it is unconfigured. Callers (e.g. an actor's own wiring code
      choosing which semantic_memory to inject) must handle None by
      falling back to the existing kernel/semantic_memory.py path.
    - require=True: raises MossUnavailableError with an actionable
      message if not configured, or if the `moss` package is not
      installed, rather than silently returning a backend with no real
      knowledge behind it.
    """
    project_id = os.environ.get("MOSS_PROJECT_ID", "").strip()
    project_key = os.environ.get("MOSS_PROJECT_KEY", "").strip()
    if not project_id or not project_key:
        if require:
            raise MossUnavailableError(
                "MOSS_PROJECT_ID and MOSS_PROJECT_KEY must both be set to use Moss retrieval",
            )
        return None

    try:
        from moss import MossClient
    except ImportError as exc:
        if require:
            raise MossUnavailableError(f"the 'moss' package is not installed: {exc}") from exc
        logger.warning("MOSS_PROJECT_ID/KEY are set but the 'moss' package is not installed -- falling back")
        return None

    client = MossClient(project_id, project_key)
    return MossSemanticMemory(client, index_name=index_name)
