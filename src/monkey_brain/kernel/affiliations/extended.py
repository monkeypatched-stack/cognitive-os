"""Extended AffiliationManager — Migrates KnowledgeGraph capabilities.

Extends the base AffiliationManager with:
- Typed entities (Person, Organization, Asset, etc.)
- Rich relationships (WORKS_FOR, OWNS, HOLDINGS, etc.)
- Temporal snapshots
- Domain-specific queries
- Knowledge graph traversal

This migration unifies the relationship and knowledge graph systems
under a single manager while maintaining backward compatibility.

DRY: Imports Entity, KnowledgeRelationship, TemporalSnapshot from knowledge_graph.py
     instead of duplicating them.
"""

from __future__ import annotations

import logging
from typing import Any

from .manager import AffiliationManager
from .affiliation import Affiliation
from .trust import TrustEngine
from ..knowledge_graph import (
    Entity,
    Relationship as KnowledgeRelationship,
    TemporalSnapshot,
    EntityType,
    RelationshipType,
)

logger = logging.getLogger("agentos.affiliations.extended")


class ExtendedAffiliationManager(AffiliationManager):
    """Extended AffiliationManager with KnowledgeGraph capabilities.

    Inherits from AffiliationManager and adds:
    - Entity storage and management
    - Typed relationships
    - Temporal snapshots
    - Domain-specific queries
    - Graph traversal

    DRY: Reuses Entity, KnowledgeRelationship, TemporalSnapshot from knowledge_graph.py
    SOLID: Single responsibility - orchestrates entity/relationship operations
    """

    def __init__(self, trust_engine: TrustEngine | None = None, person_id: str = ""):
        super().__init__(trust_engine)
        self.person_id = person_id
        self._entities: dict[str, Entity] = {}
        self._knowledge_relationships: dict[str, KnowledgeRelationship] = {}
        self._adjacency: dict[str, list[str]] = {}
        self._reverse_adjacency: dict[str, list[str]] = {}
        self._snapshots: list[TemporalSnapshot] = []

    # ── Entity Operations ───────────────────────────────────────

    def add_entity(
        self,
        entity_id: str = "",
        entity_type: str = EntityType.OTHER,
        name: str = "",
        attributes: dict | None = None,
        **kwargs,
    ) -> Entity:
        """Add an entity to the knowledge graph."""
        entity = Entity(entity_id, entity_type, name, attributes or kwargs)
        self._entities[entity.entity_id] = entity

        # LSP: Also create an affiliation for backward compatibility
        aff_id = f"entity:{entity.entity_id}"
        aff = Affiliation(
            affiliation_id=aff_id,
            affiliation_type=entity_type,
            target_id=entity.entity_id,
            target_name=name,
            trust_level=0.5,
        )
        self.add(aff)

        return entity

    def get_entity(self, entity_id: str) -> Entity | None:
        """Get an entity by ID."""
        return self._entities.get(entity_id)

    def update_entity(self, entity_id: str, **kwargs) -> Entity | None:
        """Update an entity."""
        entity = self._entities.get(entity_id)
        if entity:
            entity.update(**kwargs)
        return entity

    def remove_entity(self, entity_id: str) -> bool:
        """Remove an entity and its relationships."""
        if entity_id not in self._entities:
            return False

        # Remove knowledge relationships
        rel_ids = self._adjacency.get(entity_id, []) + self._reverse_adjacency.get(entity_id, [])
        for rel_id in rel_ids:
            self._knowledge_relationships.pop(rel_id, None)

        del self._entities[entity_id]
        self._adjacency.pop(entity_id, None)
        self._reverse_adjacency.pop(entity_id, None)

        return True

    def entities_by_type(self, entity_type: str) -> list[Entity]:
        """Get all entities of a specific type."""
        return [e for e in self._entities.values() if e.entity_type == entity_type]

    def entities_by_name(self, name: str) -> list[Entity]:
        """Get entities by name."""
        return [e for e in self._entities.values() if e.name == name]

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    # ── Knowledge Relationship Operations ───────────────────────

    def add_knowledge_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str = RelationshipType.RELATED_TO,
        attributes: dict | None = None,
        confidence: float = 1.0,
    ) -> KnowledgeRelationship:
        """Add a knowledge relationship."""
        import uuid

        rel = KnowledgeRelationship(
            source_id=source_id,
            target_id=target_id,
            relationship_type=relationship_type,
            attributes=attributes or {},
            confidence=confidence,
        )
        rel.relationship_id = str(uuid.uuid4())  # DRY: Generate UUID
        self._knowledge_relationships[rel.relationship_id] = rel
        self._adjacency.setdefault(source_id, []).append(rel.relationship_id)
        self._reverse_adjacency.setdefault(target_id, []).append(rel.relationship_id)

        # LSP: Also create an affiliation for backward compatibility
        aff_id = f"rel:{rel.relationship_id}"
        aff = Affiliation(
            affiliation_id=aff_id,
            affiliation_type=relationship_type.lower(),
            target_id=target_id,
            target_name=target_id,
            trust_level=confidence,
        )
        self.add(aff)

        return rel

    def get_knowledge_relationship(self, relationship_id: str) -> KnowledgeRelationship | None:
        """Get a knowledge relationship by ID."""
        return self._knowledge_relationships.get(relationship_id)

    def remove_knowledge_relationship(self, relationship_id: str) -> bool:
        """Remove a knowledge relationship."""
        rel = self._knowledge_relationships.pop(relationship_id, None)
        if rel is None:
            return False

        source_list = self._adjacency.get(rel.source_id, [])
        if relationship_id in source_list:
            source_list.remove(relationship_id)

        target_list = self._reverse_adjacency.get(rel.target_id, [])
        if relationship_id in target_list:
            target_list.remove(relationship_id)

        return True

    def knowledge_relationships_for(self, entity_id: str) -> list[KnowledgeRelationship]:
        """Get knowledge relationships involving an entity."""
        results = []
        for rel_id in self._adjacency.get(entity_id, []):
            rel = self._knowledge_relationships.get(rel_id)
            if rel and rel.source_id == entity_id:
                results.append(rel)
        for rel_id in self._reverse_adjacency.get(entity_id, []):
            rel = self._knowledge_relationships.get(rel_id)
            if rel and rel.target_id == entity_id:
                results.append(rel)
        return results

    def knowledge_relationships_by_type(self, relationship_type: str) -> list[KnowledgeRelationship]:
        """Get all knowledge relationships of a specific type."""
        return [r for r in self._knowledge_relationships.values() if r.relationship_type == relationship_type]

    @property
    def knowledge_relationship_count(self) -> int:
        return len(self._knowledge_relationships)

    # ── Temporal Operations ─────────────────────────────────────

    def create_snapshot(self, year: int) -> TemporalSnapshot:
        """Create a temporal snapshot."""
        import uuid

        snapshot = TemporalSnapshot(
            snapshot_id=str(uuid.uuid4()),
            entity_id=self.person_id,
            year=year,
        )
        snapshot.attributes = {e.entity_id: e.attributes for e in self._entities.values()}
        snapshot.relationships = list(self._knowledge_relationships.keys())
        self._snapshots.append(snapshot)
        return snapshot

    def get_snapshots(self, year: int | None = None) -> list[TemporalSnapshot]:
        """Get snapshots, optionally filtered by year."""
        if year:
            return [s for s in self._snapshots if s.year == year]
        return list(self._snapshots)

    @property
    def snapshot_count(self) -> int:
        return len(self._snapshots)

    # ── Domain-Specific Queries ─────────────────────────────────

    def _entities_by_relationship_type(self, rel_type: str) -> list[Entity]:
        """DRY: Single method for all domain queries."""
        return [
            self._entities[r.target_id]
            for r in self._knowledge_relationships.values()
            if r.source_id == self.person_id and r.relationship_type == rel_type and r.target_id in self._entities
        ]

    def get_family(self) -> list[Entity]:
        """Get family members."""
        family_types = [
            RelationshipType.MARRIED_TO,
            RelationshipType.PARENT_OF,
            RelationshipType.CHILD_OF,
            RelationshipType.SIBLING_OF,
            RelationshipType.DEPENDENT_OF,
        ]
        family_ids = set()
        for rel in self._knowledge_relationships.values():
            if rel.relationship_type in family_types:
                if rel.source_id == self.person_id:
                    family_ids.add(rel.target_id)
                elif rel.target_id == self.person_id:
                    family_ids.add(rel.source_id)
        return [self._entities[eid] for eid in family_ids if eid in self._entities]

    def get_employers(self) -> list[Entity]:
        """Get employers."""
        return self._entities_by_relationship_type(RelationshipType.WORKS_FOR)

    def get_assets(self) -> list[Entity]:
        """Get owned assets."""
        results = self._entities_by_relationship_type(RelationshipType.OWNS)
        results.extend(self._entities_by_relationship_type(RelationshipType.POSSESSES))
        return results

    def get_investments(self) -> list[Entity]:
        """Get investments."""
        return self._entities_by_relationship_type(RelationshipType.HOLDINGS)

    def get_income_sources(self) -> list[Entity]:
        """Get income sources."""
        return self._entities_by_relationship_type(RelationshipType.RECEIVES_INCOME)

    def get_businesses(self) -> list[Entity]:
        """Get businesses."""
        return self._entities_by_relationship_type(RelationshipType.OWNS_BUSINESS)

    # ── Graph Traversal ─────────────────────────────────────────

    def find_path(self, source_id: str, target_id: str, max_hops: int = 5) -> list[KnowledgeRelationship]:
        """Find path between two entities using BFS."""
        if source_id == target_id:
            return []

        from collections import deque

        queue = deque([(source_id, [])])
        visited = {source_id}

        while queue:
            current, path = queue.popleft()

            if len(path) >= max_hops:
                continue

            for rel in self.knowledge_relationships_for(current):
                next_entity = rel.target_id if rel.source_id == current else rel.source_id

                if next_entity == target_id:
                    return path + [rel]

                if next_entity not in visited:
                    visited.add(next_entity)
                    queue.append((next_entity, path + [rel]))

        return []

    def connected_entities(self, entity_id: str) -> list[str]:
        """Get all entities connected to an entity."""
        connected = set()
        for rel in self.knowledge_relationships_for(entity_id):
            if rel.source_id == entity_id:
                connected.add(rel.target_id)
            else:
                connected.add(rel.source_id)
        return sorted(connected)

    # ── Statistics ──────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Get graph statistics."""
        entity_types = {}
        for e in self._entities.values():
            entity_types[e.entity_type] = entity_types.get(e.entity_type, 0) + 1

        rel_types = {}
        for r in self._knowledge_relationships.values():
            rel_types[r.relationship_type] = rel_types.get(r.relationship_type, 0) + 1

        return {
            "person_id": self.person_id,
            "affiliation_count": self.count(),
            "entity_count": self.entity_count,
            "knowledge_relationship_count": self.knowledge_relationship_count,
            "snapshot_count": self.snapshot_count,
            "entity_types": entity_types,
            "relationship_types": rel_types,
        }

    # ── Serialization ───────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        base = super().to_dict()
        base.update(
            {
                "person_id": self.person_id,
                "entities": {k: v.to_dict() for k, v in self._entities.items()},
                "knowledge_relationships": {k: v.to_dict() for k, v in self._knowledge_relationships.items()},
                "snapshots": [s.to_dict() for s in self._snapshots],
            }
        )
        return base

    @classmethod
    def from_dict(cls, data: dict[str, Any], trust_engine: TrustEngine | None = None) -> ExtendedAffiliationManager:
        """Deserialize from dict."""
        mgr = cls(trust_engine=trust_engine, person_id=data.get("person_id", ""))

        # Restore base affiliations
        for ad in data.get("affiliations", []):
            aff = Affiliation(
                affiliation_id=ad.get("affiliation_id", ""),
                affiliation_type=ad.get("affiliation_type", ""),
                target_id=ad.get("target_id", ""),
                target_name=ad.get("target_name", ""),
                trust_level=ad.get("trust_level", 0.5),
            )
            mgr.add(aff)

        # Restore entities
        for eid, edata in data.get("entities", {}).items():
            mgr._entities[eid] = Entity.from_dict(edata)

        # Restore knowledge relationships
        for rid, rdata in data.get("knowledge_relationships", {}).items():
            rel = KnowledgeRelationship.from_dict(rdata)
            mgr._knowledge_relationships[rid] = rel
            mgr._adjacency.setdefault(rel.source_id, []).append(rid)
            mgr._reverse_adjacency.setdefault(rel.target_id, []).append(rid)

        # Restore snapshots
        for sdata in data.get("snapshots", []):
            mgr._snapshots.append(TemporalSnapshot.from_dict(sdata))

        return mgr
