"""KnowledgeGraphMemoryAdapter — the graph_client kernel/learn/memory/
manager.py::MemoryManager needs, backed by the existing, already-working
kernel/knowledge_graph.py::KnowledgeGraph instead of a real Neo4j server.

MemoryManager.persist_to_episodic_stream() calls
`graph_db.insert_node(node_id, payload, label=...)` — this adapter
translates that single call into KnowledgeGraph.add_entity(), storing
`label` in the entity's attributes (KnowledgeGraph's EntityType enum is a
closed, domain-specific taxonomy — person/asset/investment/... — with no
generic "memory trace" member, so every memory node is added as
EntityType.OTHER and the caller-supplied label is preserved as data
instead of forced into that enum).
"""

from __future__ import annotations

from typing import Any

from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph, EntityType


class KnowledgeGraphMemoryAdapter:
    def __init__(self, knowledge_graph: KnowledgeGraph) -> None:
        self._graph = knowledge_graph

    def insert_node(
        self,
        node_id: str,
        payload: dict[str, Any],
        label: str = "",
        name: str = "",
    ) -> None:
        # `label` stays the structural marker (Neo4j-style node label,
        # e.g. "EpisodicTrace"/"ProceduralPolicy") that cognitive_gc.py /
        # compactor.py match on — it must not change. `name` is what
        # KnowledgeGraph.entities_by_keywords indexes for content search,
        # so a content-derived name lets other actors' goal keywords
        # actually find what was recorded, instead of every node sharing
        # one indistinguishable constant name.
        self._graph.add_entity(
            entity_id=node_id,
            entity_type=EntityType.OTHER,
            name=name or label,
            attributes={"label": label, **payload},
        )
