"""End-to-end: SittingFace chart -> retrieval -> planning context -> LLM prompt."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from src.monkey_brain.kernel.pipeline.planning.context_engine import (
    ContextConstructionEngine,
)
from src.monkey_brain.kernel.pipeline.belief_state import Goal
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

DISTINCT_FACT = "ETASS-UNIQUE-FACT-7f3a: SittingFace charts compile into runtime capabilities."


@dataclass
class _Chart:
    name: str
    chart_type: str
    values: dict[str, Any] = field(default_factory=dict)
    source_path: str = "/somatic/charts/etass-runtime"


class _Compiler:
    charts = [
        _Chart(
            name="etass-runtime",
            chart_type="module",
            values={"module": {"description": DISTINCT_FACT}},
        ),
    ]

    def search(self, query: str) -> list[dict]:
        if "etass" in query.lower() or "sittingface" in query.lower():
            return [
                {
                    "name": "etass-runtime",
                    "chart_type": "module",
                    "matched_in": ["name", "module.description"],
                    "source_path": "/somatic/charts/etass-runtime",
                }
            ]
        return []


@pytest.mark.asyncio
async def test_sittingface_knowledge_reaches_final_llm_input(monkeypatch):
    from src.monkey_brain.kernel.plan.intents import intent_registry

    monkeypatch.setattr(intent_registry, "get_somatic_compiler", lambda: _Compiler())

    engine = ContextConstructionEngine(planetary_runtime=PlanetaryRuntime())
    goal = Goal(
        name="ETASS architecture",
        description="How does SittingFace integrate with ETASS runtime?",
    )
    planning_context = await engine.build_async("actor-e2e", goal, execution_id="e2e-exec-1")

    assert any(DISTINCT_FACT in item.content for item in planning_context.relevant_external_knowledge)

    class _LLMStub:
        last_prompt = ""

        async def complete(self, prompt, system="", max_tokens=None, **kwargs):
            _LLMStub.last_prompt = prompt
            return '{"steps": [{"action": "RespondToInquiry", "description": "answer", "parameters": {"answer": "ok"}, "expected_outcome": "done", "cost": 0.1, "confidence": 0.9}], "summary": "done", "confidence": 0.9}'

    planner = LLMPlanner(backend=_LLMStub())
    await planner.plan(planning_context)

    assert DISTINCT_FACT in _LLMStub.last_prompt
    assert planning_context.metadata.get("external_knowledge_retrieval", {}).get("injected") is True
