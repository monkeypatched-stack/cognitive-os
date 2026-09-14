"""Tests for the Context-Aware Personalized Planning refactor:
PlanningContext assembly (ContextConstructionEngine), ranking/dedup/
compression, provenance, LLMPlanner's dual-accepting plan()
signature, and actor-specific personalization.
"""

from __future__ import annotations

import asyncio
import json

from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
)
from src.monkey_brain.kernel.learn.memory.manager import MemoryManager
from src.monkey_brain.kernel.learn.memory.vector_backend import InMemoryVectorBackend
from src.monkey_brain.kernel.learn.memory.graph_adapter import (
    KnowledgeGraphMemoryAdapter,
)
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
from src.monkey_brain.kernel.pipeline.planning.context_engine import (
    ContextConstructionEngine,
)
from src.monkey_brain.kernel.pipeline.planning.domain import (
    PlanningContext,
    RetrievedItem,
)
from src.monkey_brain.kernel.pipeline.belief_state import Goal, BeliefState
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner


class _FakeBackend:
    def __init__(self, response: str):
        self.response = response
        self.last_prompt = None

    async def complete(self, prompt, system="", max_tokens=None, **kwargs):
        self.last_prompt = prompt
        return self.response


class _PersonalizingFakeBackend:
    """Stands in for a real LLM actually reading the prompt's retrieved
    experiences: returns a different canned plan depending on whether the
    actor's preferred store appears in the prompt text — proving
    personalization flows through the pipeline (what the backend is given
    to reason about), not a hardcoded multiplier in kernel code."""

    async def complete(self, prompt, system="", max_tokens=None, **kwargs):
        # Distinguish on the *experience* signal ("shops at wholefoods"),
        # not the shared fact data (both actors' belief.facts mention
        # "wholefoods" identically) — this is what actually differs
        # between Alice's and Bob's retrieved context.
        preferred = "wholefoods" if "shops at wholefoods" in prompt.lower() else None
        store = preferred or "costco"
        return json.dumps(
            {
                "steps": [
                    {
                        "action": f"buy_at_{store}",
                        "description": f"Buy milk at {store}" + (" [PREFERRED]" if preferred else ""),
                        "expected_outcome": "milk acquired",
                        "cost": 0.1,
                        "confidence": 0.85,
                    }
                ],
                "summary": f"Buy at {store}",
                "confidence": 0.85,
            }
        )


def _make_engine():
    pr = PlanetaryRuntime()
    mm = MemoryManager(InMemoryVectorBackend(), KnowledgeGraphMemoryAdapter(KnowledgeGraph()))
    kg = KnowledgeGraph()
    engine = ContextConstructionEngine(planetary_runtime=pr, memory_manager=mm, knowledge_graph=kg)
    return pr, mm, kg, engine


def _register(pr, name):
    return pr.register_actor(ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN)))


# ── PlanningContext assembly is actor-specific ───────────────────────────


def test_two_actors_same_goal_different_planning_contexts():
    pr, mm, kg, engine = _make_engine()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")

    mm.record_experience(alice.actor_id, "experience", "Alice has a Costco membership and owns a car")
    mm.record_experience(bob.actor_id, "experience", "Bob is a student who walks everywhere")

    # description intentionally matches name (no words beyond it): the
    # engine's distinctive-keyword gate only activates on words in the
    # goal text that aren't already in the goal name (see
    # ContextConstructionEngine._search_memory's own docstring) — with
    # nothing distinctive here, retrieval falls back to pure cosine
    # similarity ranking, which is what this test is actually exercising
    # (actor-specific retrieval), not keyword-gated relevance.
    goal = Goal(name="buy milk", description="buy milk")
    ctx_alice = engine.build(alice.actor_id, goal)
    ctx_bob = engine.build(bob.actor_id, goal)

    assert ctx_alice.actor_id != ctx_bob.actor_id
    assert ctx_alice.relevant_experiences != ctx_bob.relevant_experiences
    assert all(e.content for e in ctx_alice.relevant_experiences)


def test_different_contexts_produce_different_plans():
    pr, mm, kg, engine = _make_engine()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")

    mm.record_experience(alice.actor_id, "experience", "Alice frequently shops at wholefoods")
    mm.record_experience(bob.actor_id, "experience", "Bob has no strong preference")

    # description matches name — see the sibling test above for why (the
    # distinctive-keyword gate would otherwise strip both actors'
    # experiences to empty, since neither mentions "need").
    goal = Goal(name="buy milk", description="buy milk")
    ctx_alice = engine.build(alice.actor_id, goal)
    ctx_bob = engine.build(bob.actor_id, goal)

    for ctx, actor_id in ((ctx_alice, alice.actor_id), (ctx_bob, bob.actor_id)):
        belief = BeliefState(actor_id=actor_id)
        belief.add_fact(entity="costco", attribute="price", value=2.0, confidence=0.9)
        belief.add_fact(entity="wholefoods", attribute="price", value=5.0, confidence=0.9)
        ctx.metadata["_legacy_belief"] = belief

    planner = LLMPlanner(backend=_PersonalizingFakeBackend())
    plan_alice = asyncio.run(planner.plan(ctx_alice))
    plan_bob = asyncio.run(planner.plan(ctx_bob))

    descriptions_alice = [s.description for s in plan_alice.steps]
    descriptions_bob = [s.description for s in plan_bob.steps]
    assert descriptions_alice != descriptions_bob
    assert any("PREFERRED" in d for d in descriptions_alice)
    assert not any("PREFERRED" in d for d in descriptions_bob)


# ── Ranking / Deduplication ────────────────────────────────────────────────


def test_deduplication_keeps_highest_scored():
    _, _, _, engine = _make_engine()
    items = (
        RetrievedItem(
            content="same fact",
            item_type="knowledge",
            source="a",
            confidence=0.5,
            retrieval_score=0.3,
        ),
        RetrievedItem(
            content="same fact",
            item_type="knowledge",
            source="a",
            confidence=0.9,
            retrieval_score=0.9,
        ),
    )
    result = engine._rank_and_dedupe(items, set())
    assert len(result) == 1
    assert result[0].confidence == 0.9


def test_no_compression_frequent_same_source_items_all_preserved():
    _, _, _, engine = _make_engine()
    items = tuple(
        RetrievedItem(
            content=f"visited costco trip {i}",
            item_type="experience",
            source="costco",
            confidence=0.9,
            retrieval_score=0.5,
        )
        for i in range(4)
    )
    result = engine._rank_and_dedupe(items, set())
    assert len(result) == 4
    assert {r.content for r in result} == {i.content for i in items}


def test_distinct_small_groups_all_preserved():
    _, _, _, engine = _make_engine()
    items = (
        RetrievedItem(
            content="visited costco",
            item_type="experience",
            source="costco",
            confidence=0.9,
            retrieval_score=0.5,
        ),
        RetrievedItem(
            content="visited wholefoods",
            item_type="experience",
            source="wholefoods",
            confidence=0.9,
            retrieval_score=0.5,
        ),
    )
    result = engine._rank_and_dedupe(items, set())
    assert len(result) == 2
    assert {r.content for r in result} == {"visited costco", "visited wholefoods"}


def test_provenance_present_on_every_retrieved_item():
    pr, mm, kg, engine = _make_engine()
    alice = _register(pr, "Alice")
    mm.record_experience(alice.actor_id, "experience", "Alice shops at Costco")
    ctx = engine.build(alice.actor_id, Goal(name="buy milk"))
    for item in ctx.relevant_experiences:
        assert item.source
        assert item.confidence >= 0.0
        assert item.retrieval_score >= 0.0


# ── LLMPlanner dual-accepting signature ──────────────────────────────────


def test_planner_accepts_planning_context_directly():
    ctx = PlanningContext(actor_id="a1", goal=Goal(name="test_goal"))
    planner = LLMPlanner(backend=_FakeBackend(json.dumps({"steps": [], "summary": "", "confidence": 0.5})))
    plan = asyncio.run(planner.plan(ctx))
    assert plan.goal == "test_goal"


def test_planner_legacy_call_shape_unchanged():
    belief = BeliefState(actor_id="a1")
    belief.add_fact(entity="milk", attribute="stock", value=5, confidence=0.9)
    goal = Goal(name="acquire_milk")
    backend = _FakeBackend(
        json.dumps(
            {
                "steps": [
                    {
                        "action": "buy_milk",
                        "description": "Buy milk",
                        "expected_outcome": "milk",
                        "cost": 0.1,
                        "confidence": 0.9,
                    }
                ],
                "summary": "Buy milk",
                "confidence": 0.9,
            }
        )
    )
    planner = LLMPlanner(backend=backend)

    plan_two_arg = asyncio.run(planner.plan(belief, goal))
    plan_three_arg = asyncio.run(planner.plan(belief, goal, None))
    assert plan_two_arg.goal == "acquire_milk"
    assert plan_two_arg.steps == plan_three_arg.steps


def test_planner_empty_goal_returns_empty_plan():
    planner = LLMPlanner(backend=_FakeBackend("{}"))
    plan = asyncio.run(planner.plan(BeliefState(actor_id="a1"), Goal(name="")))
    assert plan.confidence == 0.0
    assert plan.steps == ()


# ── MemoryManager semantic search ────────────────────────────────────────


def test_memory_manager_search_ranks_by_similarity():
    mm = MemoryManager(InMemoryVectorBackend(), KnowledgeGraphMemoryAdapter(KnowledgeGraph()))
    mm.record_experience("a1", "experience", "Alice frequently shops at Costco for bulk groceries")
    mm.record_experience("a1", "experience", "Alice visited a car dealership")

    results = mm.search_episodic("grocery shopping at Costco", top_k=2)
    assert results[0].payload["text"] == "Alice frequently shops at Costco for bulk groceries"


def test_memory_manager_search_filters_by_actor():
    mm = MemoryManager(InMemoryVectorBackend(), KnowledgeGraphMemoryAdapter(KnowledgeGraph()))
    mm.record_experience("alice", "experience", "shopping trip")
    mm.record_experience("bob", "experience", "shopping trip")

    results = mm.search_episodic("shopping", top_k=10, actor_id="alice")
    assert all(r.payload["actor_id"] == "alice" for r in results)
    assert len(results) == 1
