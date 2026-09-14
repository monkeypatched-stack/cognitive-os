"""Tests for the CCB-400 knowledge-sharing bug fix: record_experience must
write a content-derived Entity.name (not a shared constant), so
KnowledgeGraph.entities_by_keywords can find a recorded experience from
another actor's goal keywords — while the structural "EpisodicTrace"/
"ProceduralPolicy" label (which cognitive_gc.py/compactor.py match on via
Cypher) stays exactly as before.
"""

from __future__ import annotations

from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
from src.monkey_brain.kernel.learn.memory.graph_adapter import (
    KnowledgeGraphMemoryAdapter,
)
from src.monkey_brain.kernel.learn.memory.manager import MemoryManager
from src.monkey_brain.kernel.learn.memory.vector_backend import InMemoryVectorBackend


def _manager() -> tuple[MemoryManager, KnowledgeGraph]:
    kg = KnowledgeGraph()
    mm = MemoryManager(InMemoryVectorBackend(), KnowledgeGraphMemoryAdapter(kg))
    return mm, kg


def test_record_experience_creates_a_content_derived_name():
    mm, kg = _manager()
    mm.record_experience(
        "costco",
        "supplier_discovery",
        "Supplier X reduces spoilage for milk by using refrigerated trucks",
    )
    entities = kg.entities_by_keywords(["spoilage"])
    assert len(entities) == 1
    assert "spoilage" in entities[0].name.lower()
    assert entities[0].name != "EpisodicTrace"


def test_record_experience_is_findable_by_another_actors_goal_keywords():
    mm, kg = _manager()
    mm.record_experience(
        "costco",
        "supplier_discovery",
        "Supplier X reduces spoilage for milk by using refrigerated trucks",
    )
    # Aldi's own goal text, tokenized the same way context_engine.py's
    # _explore_knowledge does (goal_text.lower().split(), len > 2).
    goal_text = "find the best milk supplier and reduce spoilage"
    keywords = [w for w in goal_text.lower().split() if len(w) > 2]
    entities = kg.entities_by_keywords(keywords)
    assert len(entities) == 1
    assert entities[0].attributes["actor_id"] == "costco"


def test_record_experience_preserves_episodic_trace_structural_label():
    mm, kg = _manager()
    mm.record_experience("costco", "supplier_discovery", "Supplier X reduces spoilage")
    entities = kg.entities_by_keywords(["spoilage"])
    assert len(entities) == 1
    # attributes["label"] is the structural marker cognitive_gc.py/
    # compactor.py's Cypher MATCH (e:EpisodicTrace) queries depend on —
    # must stay the constant, unaffected by the content-derived name fix.
    assert entities[0].attributes["label"] == "EpisodicTrace"


def test_two_distinct_experiences_get_distinguishable_names():
    mm, kg = _manager()
    mm.record_experience("costco", "supplier_discovery", "Supplier X reduces spoilage")
    mm.record_experience("walmart", "supplier_discovery", "Supplier Y delivers late frequently")
    spoilage = kg.entities_by_keywords(["spoilage"])
    late = kg.entities_by_keywords(["late"])
    assert len(spoilage) == 1
    assert len(late) == 1
    assert spoilage[0].entity_id != late[0].entity_id
    assert spoilage[0].name != late[0].name


def test_insert_node_without_name_falls_back_to_label_unaffected():
    """Mirrors compactor.py's call shape (label only, no name) — must
    behave exactly as before the fix: name defaults to label."""
    kg = KnowledgeGraph()
    adapter = KnowledgeGraphMemoryAdapter(kg)
    adapter.insert_node("policy_1", {"pattern_id": "p1"}, label="ProceduralPolicy")
    entity = kg.get_entity("policy_1")
    assert entity is not None
    assert entity.name == "ProceduralPolicy"
    assert entity.attributes["label"] == "ProceduralPolicy"
