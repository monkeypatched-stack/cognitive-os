"""Cognitive Network (CCB-600), Phase 1: Alice publishes a `visibility=
"shared"` experience, Bob retrieves it during planning if — and only if —
they currently share at least one active Society membership. Covers both
retrieval paths ContextConstructionEngine has: the CognitiveMemory search
pass (_search_memory) and knowledge-graph keyword exploration
(_explore_knowledge), since MemoryManager.record_experience() writes into
the same shared KnowledgeGraph both paths read from.
"""

from __future__ import annotations

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
from src.monkey_brain.kernel.pipeline.belief_state import Goal


def _make_engine():
    pr = PlanetaryRuntime()
    kg = KnowledgeGraph()
    mm = MemoryManager(InMemoryVectorBackend(), KnowledgeGraphMemoryAdapter(kg))
    engine = ContextConstructionEngine(planetary_runtime=pr, memory_manager=mm, knowledge_graph=kg)
    return pr, mm, kg, engine


def _register(pr, name):
    return pr.register_actor(ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN)))


def _make_household(pr, *actor_ids):
    household = pr.create_society(name="The Household", society_type="household")
    for actor_id in actor_ids:
        pr.join_society(actor_id, household.society_id)
    return household


# ── _search_memory: shared experiences reach a co-member's PlanningContext ──


def test_shared_experience_reaches_co_member():
    pr, mm, kg, engine = _make_engine()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")
    _make_household(pr, alice.actor_id, bob.actor_id)

    mm.record_experience(
        alice.actor_id,
        "experience",
        "Costco every Tuesday, 20% cheaper",
        {"visibility": "shared"},
    )

    ctx_bob = engine.build(bob.actor_id, Goal(name="buy milk", description="need milk, costco tuesday"))

    shared = [e for e in ctx_bob.relevant_experiences if e.source == "cognitive_memory_shared"]
    assert any("Costco every Tuesday" in e.content for e in shared)
    assert all(e.evidence_ids == (f"actor:{alice.actor_id}",) for e in shared)


def test_private_experience_never_leaks_to_co_member():
    pr, mm, kg, engine = _make_engine()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")
    _make_household(pr, alice.actor_id, bob.actor_id)

    mm.record_experience(alice.actor_id, "experience", "Alice's private grocery notes")

    ctx_bob = engine.build(bob.actor_id, Goal(name="buy milk", description="private grocery notes"))

    assert not any("private grocery notes" in e.content for e in ctx_bob.relevant_experiences)


def test_shared_experience_does_not_reach_disjoint_society_actor():
    pr, mm, kg, engine = _make_engine()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")
    _make_household(pr, alice.actor_id)  # Bob is not a member of Alice's household

    mm.record_experience(
        alice.actor_id,
        "experience",
        "Costco every Tuesday, 20% cheaper",
        {"visibility": "shared"},
    )

    ctx_bob = engine.build(bob.actor_id, Goal(name="buy milk", description="costco tuesday"))

    assert not any("Costco every Tuesday" in e.content for e in ctx_bob.relevant_experiences)


def test_shared_experience_does_not_reach_actor_with_no_memberships_at_all():
    pr, mm, kg, engine = _make_engine()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")
    # Neither actor has any society membership.

    mm.record_experience(
        alice.actor_id,
        "experience",
        "Costco every Tuesday, 20% cheaper",
        {"visibility": "shared"},
    )

    ctx_bob = engine.build(bob.actor_id, Goal(name="buy milk", description="costco tuesday"))

    assert not any("Costco every Tuesday" in e.content for e in ctx_bob.relevant_experiences)


def test_own_top_k_not_starved_by_shared_pass():
    pr, mm, kg, engine = _make_engine()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")
    _make_household(pr, alice.actor_id, bob.actor_id)

    mm.record_experience(alice.actor_id, "experience", "shared costco tip", {"visibility": "shared"})
    for i in range(5):
        mm.record_experience(bob.actor_id, "experience", f"Bob's own costco trip {i}")

    ctx_bob = engine.build(bob.actor_id, Goal(name="buy milk", description="costco"))

    own = [e for e in ctx_bob.relevant_experiences if e.source == "cognitive_memory"]
    assert len(own) == 5


# ── _explore_knowledge: same visibility rule applies to the keyword-search path ──


def test_explore_knowledge_does_not_leak_private_experience():
    pr, mm, kg, engine = _make_engine()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")
    _make_household(pr, alice.actor_id, bob.actor_id)

    mm.record_experience(alice.actor_id, "experience", "confidential budget review notes")

    ctx_bob = engine.build(
        bob.actor_id,
        Goal(name="review", description="confidential budget review notes"),
    )

    assert not any("confidential" in k.content.lower() for k in ctx_bob.relevant_knowledge)


def test_explore_knowledge_never_surfaces_experiences_even_when_shared():
    """_may_explore_entity excludes EVERY EpisodicTrace entity outright,
    regardless of visibility -- "relevant_knowledge is 'what does the KG
    say about the world,' not 'what has this actor experienced'" (see its
    own docstring). Before this exclusion, an actor's own/another actor's
    experience entries leaked into relevant_knowledge as nonsense
    entities like "experience: Completed goal: ..." -- confirmed live.
    A shared experience still correctly reaches a co-member, just via
    relevant_experiences (test_shared_experience_reaches_co_member), not
    this keyword-search path."""
    pr, mm, kg, engine = _make_engine()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")
    _make_household(pr, alice.actor_id, bob.actor_id)

    mm.record_experience(
        alice.actor_id,
        "experience",
        "holidaydeal seasonal discount",
        {"visibility": "shared"},
    )

    ctx_bob = engine.build(bob.actor_id, Goal(name="shop", description="holidaydeal seasonal discount"))

    assert not any("holidaydeal" in k.content.lower() for k in ctx_bob.relevant_knowledge)
    shared = [e for e in ctx_bob.relevant_experiences if e.source == "cognitive_memory_shared"]
    assert any("holidaydeal" in e.content.lower() for e in shared)


def test_explore_knowledge_unfiltered_for_non_episodic_entities():
    pr, mm, kg, engine = _make_engine()
    bob = _register(pr, "Bob")
    kg.add_entity(entity_id="store-1", name="Costco Warehouse", attributes={})

    ctx_bob = engine.build(bob.actor_id, Goal(name="shop", description="costco warehouse"))

    assert any("Costco Warehouse" in k.content for k in ctx_bob.relevant_knowledge)


# ── search_shared_experiences: public retrieval entry point ─────────────────


def test_search_shared_experiences_public_method_matches_gate():
    pr, mm, kg, engine = _make_engine()
    alice = _register(pr, "Alice")
    bob = _register(pr, "Bob")
    _make_household(pr, alice.actor_id, bob.actor_id)

    mm.record_experience(
        alice.actor_id,
        "experience",
        "Costco every Tuesday, 20% cheaper",
        {"visibility": "shared"},
    )
    mm.record_experience(alice.actor_id, "experience", "Alice's private notes")

    results = engine.search_shared_experiences(bob.actor_id, "costco tuesday")
    texts = [n.payload["text"] for n in results]
    assert "Costco every Tuesday, 20% cheaper" in texts
    assert "Alice's private notes" not in texts
