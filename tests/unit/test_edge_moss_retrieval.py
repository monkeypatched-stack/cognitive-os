"""MossSemanticMemory (kernel/edge/moss_retrieval.py) -- proves the
narrowed-scope Moss integration: a real drop-in for
SittingFaceKnowledgeRetriever's semantic_memory.query() contract, always
tagging retrieval_method="vector" honestly, always degrading to empty
results (never raising) on any Moss-side failure, and never touching
kernel/edge/local_store.py (SQLite) at all.

Uses a FAKE MossClient/SessionIndex (dependency-injected) for the
contract -- this environment has no real MOSS_PROJECT_ID/MOSS_PROJECT_KEY,
so a real-credentials-gated smoke test honestly skips, matching
tests/unit/test_ros_integration_contract.py's real-vs-fake convention.
"""
from __future__ import annotations

import os

import pytest

from src.monkey_brain.kernel.edge.moss_retrieval import (
    MossSemanticMemory,
    MossUnavailableError,
    build_moss_semantic_memory,
)

_REAL_MOSS_CONFIGURED = bool(os.environ.get("MOSS_PROJECT_ID")) and bool(os.environ.get("MOSS_PROJECT_KEY"))


class _FakeDoc:
    def __init__(self, text: str, metadata: dict | None = None, score: float = 0.9) -> None:
        self.text = text
        self.metadata = metadata or {}
        self.score = score


class _FakeSearchResult:
    def __init__(self, docs: list[_FakeDoc]) -> None:
        self.docs = docs


class _FakeSession:
    def __init__(self, *, query_result: list[_FakeDoc] | None = None, raise_on_query: Exception | None = None) -> None:
        self._query_result = query_result or []
        self._raise_on_query = raise_on_query
        self.added_docs: list = []
        self.queries: list[str] = []

    async def add_docs(self, docs, options=None):
        self.added_docs.extend(docs)
        return (len(docs), 0)

    async def query(self, query, options=None):
        self.queries.append(query)
        if self._raise_on_query:
            raise self._raise_on_query
        return _FakeSearchResult(self._query_result)


class _FakeClient:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session
        self.session_calls = []

    async def session(self, index_name, model_id=None):
        self.session_calls.append(index_name)
        return self._session


class TestQueryContractMatchesSemanticMemory:
    @pytest.mark.asyncio
    async def test_query_returns_the_exact_shape_sittingface_expects(self):
        session = _FakeSession(query_result=[_FakeDoc("CAPA is a corrective action process", {"name": "capa_chart"}, 0.87)])
        memory = MossSemanticMemory(_FakeClient(session))

        result = await memory.query("what is CAPA")

        assert isinstance(result, dict)
        assert "results" in result
        assert result["results"][0]["text"] == "CAPA is a corrective action process"
        assert result["results"][0]["metadata"] == {"name": "capa_chart"}
        assert result["results"][0]["score"] == pytest.approx(0.87)

    @pytest.mark.asyncio
    async def test_results_are_always_tagged_vector(self):
        """Moss is purely embedding-based -- this is the one place a tag
        is asserted rather than forwarded, and that is honest here."""
        session = _FakeSession(query_result=[_FakeDoc("fact")])
        memory = MossSemanticMemory(_FakeClient(session))

        result = await memory.query("q")

        assert result["results"][0]["retrieval_method"] == "vector"

    @pytest.mark.asyncio
    async def test_empty_text_documents_are_skipped(self):
        session = _FakeSession(query_result=[_FakeDoc("   "), _FakeDoc("real fact")])
        memory = MossSemanticMemory(_FakeClient(session))

        result = await memory.query("q")

        assert len(result["results"]) == 1
        assert result["results"][0]["text"] == "real fact"

    @pytest.mark.asyncio
    async def test_no_results_yet_is_a_valid_empty_response(self):
        session = _FakeSession(query_result=[])
        memory = MossSemanticMemory(_FakeClient(session))

        result = await memory.query("nothing indexed yet")

        assert result == {"results": []}


class TestFailsSoftNeverRaises:
    @pytest.mark.asyncio
    async def test_moss_query_exception_degrades_to_empty_results(self):
        session = _FakeSession(raise_on_query=ConnectionError("moss unreachable"))
        memory = MossSemanticMemory(_FakeClient(session))

        result = await memory.query("q")

        assert result == {"results": []}

    @pytest.mark.asyncio
    async def test_session_construction_failure_degrades_to_empty_results(self):
        class _BrokenClient:
            async def session(self, index_name, model_id=None):
                raise PermissionError("invalid credentials")

        memory = MossSemanticMemory(_BrokenClient())
        result = await memory.query("q")

        assert result == {"results": []}


class TestIndexDocuments:
    @pytest.mark.asyncio
    async def test_index_documents_calls_add_docs_and_returns_added_count(self):
        session = _FakeSession()
        memory = MossSemanticMemory(_FakeClient(session))

        added = await memory.index_documents([
            {"id": "1", "text": "fact one", "metadata": {"chart": "c1"}},
            {"id": "2", "text": "fact two"},
        ])

        assert added == 2
        assert len(session.added_docs) == 2

    @pytest.mark.asyncio
    async def test_session_is_reused_across_calls(self):
        session = _FakeSession()
        client = _FakeClient(session)
        memory = MossSemanticMemory(client)

        await memory.query("a")
        await memory.query("b")

        assert len(client.session_calls) == 1, "the session must be created once and reused, not per-query"


class TestBuildMossSemanticMemoryStartupBehavior:
    def test_unconfigured_returns_none_never_crashes(self, monkeypatch):
        monkeypatch.delenv("MOSS_PROJECT_ID", raising=False)
        monkeypatch.delenv("MOSS_PROJECT_KEY", raising=False)

        assert build_moss_semantic_memory() is None

    def test_unconfigured_with_require_true_raises_actionable_error(self, monkeypatch):
        monkeypatch.delenv("MOSS_PROJECT_ID", raising=False)
        monkeypatch.delenv("MOSS_PROJECT_KEY", raising=False)

        with pytest.raises(MossUnavailableError, match="MOSS_PROJECT_ID"):
            build_moss_semantic_memory(require=True)

    def test_configured_returns_a_real_moss_semantic_memory(self, monkeypatch):
        monkeypatch.setenv("MOSS_PROJECT_ID", "test-project")
        monkeypatch.setenv("MOSS_PROJECT_KEY", "test-key")

        memory = build_moss_semantic_memory()

        assert isinstance(memory, MossSemanticMemory)


@pytest.mark.skipif(not _REAL_MOSS_CONFIGURED, reason="MOSS_PROJECT_ID/MOSS_PROJECT_KEY not set -- real Moss integration suite requires real credentials")
class TestRealMossSmoke:
    """Only runs with real credentials configured. Never faked."""

    @pytest.mark.asyncio
    async def test_real_query_against_a_live_moss_project(self):
        memory = build_moss_semantic_memory(require=True)
        result = await memory.query("test query")
        assert isinstance(result, dict)
        assert "results" in result
