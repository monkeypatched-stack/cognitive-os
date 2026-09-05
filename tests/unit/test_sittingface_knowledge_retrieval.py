"""Tests for SittingFace external knowledge retrieval and prompt injection."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from src.monkey_brain.kernel.knowledge.external_context import KnowledgeRetrievalReport
from src.monkey_brain.kernel.knowledge import sittingface_retrieval as sf_module
from src.monkey_brain.kernel.knowledge.sittingface_retrieval import (
    SittingFaceKnowledgeRetriever,
    should_retrieve_external_knowledge,
)
from src.monkey_brain.kernel.pipeline.planning.context_engine import ContextConstructionEngine
from src.monkey_brain.kernel.pipeline.planning.domain import PlanningContext
from src.monkey_brain.kernel.pipeline.belief_state import Goal
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


CAPA_SNIPPET = "CAPA means Corrective and Preventive Action for pharmaceutical quality systems."


@dataclass
class _FakeChart:
    name: str
    chart_type: str
    values: dict[str, Any] = field(default_factory=dict)
    source_path: str = "/fake/chart"


class _FakeCompiler:
    def __init__(self) -> None:
        self.charts = [
            _FakeChart(
                name="gmp-compliance",
                chart_type="capability",
                values={"capability": {"description": CAPA_SNIPPET}},
                source_path="/fake/gmp",
            ),
        ]

    def search(self, query: str) -> list[dict]:
        q = query.lower()
        if "capa" in q or "gmp" in q or "compliance" in q:
            return [{
                "name": "gmp-compliance",
                "chart_type": "capability",
                "matched_in": ["name", "capability.description"],
                "source_path": "/fake/gmp",
            }]
        return []

    def summary(self) -> dict:
        return {"total_charts": len(self.charts)}


class _FakeSemanticMemory:
    def __init__(self, *, available: bool = True, fail: bool = False) -> None:
        self.available = available
        self.fail = fail

    async def query(self, question: str) -> dict:
        if self.fail:
            raise RuntimeError("embedding failed")
        return {
            "results": [
                {
                    "text": "Vector hit: batch release requires QA sign-off.",
                    "score": 0.91,
                    "metadata": {"name": "batch-release", "source_path": "/fake/batch"},
                    "retrieval_method": "vector",
                }
            ]
        }


@pytest.fixture(autouse=True)
def _reset_retrieval_cache():
    sf_module._RETRIEVAL_CACHE.set({})
    yield
    sf_module._RETRIEVAL_CACHE.set({})


def _wire_compiler(monkeypatch, compiler: _FakeCompiler) -> None:
    from src.monkey_brain.kernel.plan.intents import intent_registry
    monkeypatch.setattr(intent_registry, "get_somatic_compiler", lambda: compiler)


class TestRetrievalPolicy:
    def test_knowledge_question_triggers_retrieval(self):
        assert should_retrieve_external_knowledge("What is CAPA in GMP compliance?")

    def test_short_transactional_query_skips_by_default(self):
        assert not should_retrieve_external_knowledge("buy milk")

    def test_meta_include_forces_retrieval(self):
        assert should_retrieve_external_knowledge("hi", meta={"include_external_knowledge": True})

    def test_meta_skip_blocks_retrieval(self):
        assert not should_retrieve_external_knowledge(
            "What is CAPA?", meta={"skip_external_knowledge": True},
        )


class TestSittingFaceKnowledgeRetriever:
    def test_keyword_retrieval_returns_provenance(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        retriever = SittingFaceKnowledgeRetriever()
        report = retriever.retrieve_sync("Explain CAPA compliance", cycle_id="t1", force=True)
        assert report.attempted
        assert report.keyword_used
        assert len(report.items) == 1
        assert "Corrective and Preventive Action" in report.items[0].content
        assert report.items[0].source_chart == "gmp-compliance"
        assert report.items[0].retrieval_method == "keyword"

    def test_no_relevant_knowledge_returns_empty(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        retriever = SittingFaceKnowledgeRetriever()
        report = retriever.retrieve_sync("buy bananas quickly", cycle_id="t2", force=True)
        assert report.items == []

    @pytest.mark.asyncio
    async def test_vector_unavailable_uses_keyword_fallback(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        retriever = SittingFaceKnowledgeRetriever(semantic_memory=_FakeSemanticMemory(available=False))
        report = await retriever.retrieve("What is CAPA?", cycle_id="t3", force=True)
        assert report.keyword_used
        assert not report.vector_used
        assert "keyword_fallback" in report.methods_used

    @pytest.mark.asyncio
    async def test_vector_and_keyword_merge_without_duplicate_chart(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        retriever = SittingFaceKnowledgeRetriever(semantic_memory=_FakeSemanticMemory())
        report = await retriever.retrieve("CAPA GMP compliance", cycle_id="t4", force=True)
        assert report.vector_used
        assert any("Corrective" in i.content for i in report.items)
        assert any("QA sign-off" in i.content for i in report.items)

    @pytest.mark.asyncio
    async def test_embedding_failure_does_not_break_retrieval(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        retriever = SittingFaceKnowledgeRetriever(semantic_memory=_FakeSemanticMemory(fail=True))
        report = await retriever.retrieve("Explain CAPA", cycle_id="t5", force=True)
        assert report.keyword_used
        assert any("Corrective" in i.content for i in report.items)

    def test_cache_prevents_duplicate_retrieval(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        retriever = SittingFaceKnowledgeRetriever()
        first = retriever.retrieve_sync("CAPA compliance", cycle_id="cycle-a", force=True)
        second = retriever.retrieve_sync("CAPA compliance", cycle_id="cycle-a", force=True)
        assert first.cache_hit is False
        assert second.cache_hit is True

    def test_provenance_preserved_in_retrieved_item(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        report = SittingFaceKnowledgeRetriever().retrieve_sync("CAPA", cycle_id="t6", force=True)
        item = report.to_retrieved_items()[0]
        assert item.item_type == "external_knowledge"
        assert item.source.startswith("sittingface:")
        assert item.evidence_ids == ("gmp-compliance",)


class TestContextAndPlannerInjection:
    def test_external_knowledge_in_planning_context(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        engine = ContextConstructionEngine(planetary_runtime=PlanetaryRuntime())
        goal = Goal(name="CAPA", description="What is CAPA in pharmaceutical manufacturing?")
        ctx = engine.build("actor-1", goal, execution_id="exec-1")
        assert any("Corrective" in i.content for i in ctx.relevant_external_knowledge)
        meta = ctx.metadata.get("external_knowledge_retrieval", {})
        assert meta.get("attempted") is True
        assert meta.get("injected") is True

    @pytest.mark.asyncio
    async def test_external_knowledge_reaches_llm_prompt(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        engine = ContextConstructionEngine(planetary_runtime=PlanetaryRuntime())
        goal = Goal(name="CAPA", description="What is CAPA in pharmaceutical manufacturing?")
        ctx = await engine.build_async("actor-1", goal, execution_id="exec-2")

        class _CaptureBackend:
            def __init__(self):
                self.last_prompt = ""

            async def complete(self, prompt, system="", max_tokens=None, **kwargs):
                self.last_prompt = prompt
                return '{"steps": [], "summary": "ok", "confidence": 0.5}'

        backend = _CaptureBackend()
        planner = LLMPlanner(backend=backend)
        await planner.plan(ctx)
        assert "Corrective and Preventive Action" in backend.last_prompt
        assert "External knowledge (SittingFace)" in backend.last_prompt

    def test_no_knowledge_still_builds_context(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        engine = ContextConstructionEngine(planetary_runtime=PlanetaryRuntime())
        goal = Goal(name="buy", description="buy milk")
        ctx = engine.build("actor-1", goal, execution_id="exec-3")
        assert ctx.relevant_external_knowledge == () or ctx.relevant_external_knowledge == tuple()

    def test_kg_knowledge_separate_from_external(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        engine = ContextConstructionEngine(planetary_runtime=PlanetaryRuntime())
        goal = Goal(name="CAPA", description="What is CAPA?")
        ctx = engine.build("actor-1", goal, execution_id="exec-4")
        external_sources = {i.source for i in ctx.relevant_external_knowledge}
        kg_sources = {i.source for i in ctx.relevant_knowledge}
        assert all(s.startswith("sittingface:") for s in external_sources if s)
        assert "knowledge_graph" in kg_sources or not kg_sources


class TestPromptCompilerInjection:
    @pytest.mark.asyncio
    async def test_prompt_compiler_injects_external_block(self, monkeypatch):
        _wire_compiler(monkeypatch, _FakeCompiler())
        from broca.agents.prompt_compiler import PromptCompilerAgent
        from etass.specification import ETASSSpec

        agent = PromptCompilerAgent()
        spec = ETASSSpec(workload="governance_review", goal="Explain CAPA requirements for batch release")
        result = await agent.handle({"spec": spec})
        prompt = result.payload["compiled_prompt"]
        assert "External Knowledge (SittingFace)" in prompt
        assert "Corrective and Preventive Action" in prompt
