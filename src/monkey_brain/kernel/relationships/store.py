"""RelationshipStore — Persistence and advanced query interface for relationships.

Provides:
- Indexed storage with fast lookups
- Advanced filtering and search
- Relationship aggregation and analytics
- Import/Export for external systems
"""

from __future__ import annotations

import logging
from typing import Any
from . import (
    RelationshipGraph,
    Relationship,
    RelationshipKind,
)

logger = logging.getLogger("agentos.relationships.store")


class RelationshipStore:
    """Advanced query and persistence layer for relationships.

    Wraps RelationshipGraph with:
    - Indexed lookups by kind, strength range, entity
    - Aggregation queries (count, average strength, etc.)
    - Bulk operations
    - Analytics and reporting
    """

    def __init__(self, graph: RelationshipGraph | None = None):
        self.graph = graph or RelationshipGraph()
        self._kind_index: dict[str, set[str]] = {}  # kind -> {rel_id}
        self._strength_buckets: dict[str, set[str]] = {}  # bucket -> {rel_id}

    # ── Indexed Add ─────────────────────────────────────────────

    def add(
        self,
        source_id: str,
        target_id: str,
        kind: RelationshipKind | str = RelationshipKind.RELATED_TO,
        strength: float = 0.5,
        **kwargs,
    ) -> Relationship:
        """Add relationship with indexing."""
        rel = self.graph.add(source_id, target_id, kind, strength, **kwargs)

        # Update kind index
        kind_key = rel.kind.value
        self._kind_index.setdefault(kind_key, set()).add(rel.relationship_id)

        # Update strength bucket
        bucket = self._strength_bucket(rel.strength)
        self._strength_buckets.setdefault(bucket, set()).add(rel.relationship_id)

        return rel

    def _strength_bucket(self, strength: float) -> str:
        """Map strength to a bucket for indexing."""
        if strength >= 0.8:
            return "strong"
        elif strength >= 0.5:
            return "medium"
        elif strength >= 0.2:
            return "weak"
        else:
            return "minimal"

    # ── Advanced Queries ────────────────────────────────────────

    def find(
        self,
        source_id: str | None = None,
        target_id: str | None = None,
        kind: RelationshipKind | None = None,
        min_strength: float = 0.0,
        max_strength: float = 1.0,
        bidirectional_only: bool = False,
    ) -> list[Relationship]:
        """Advanced query with multiple filters."""
        results = self.graph.all_relationships()  # DIP: Use public API

        if source_id:
            results = [r for r in results if r.source_id == source_id]

        if target_id:
            results = [r for r in results if r.target_id == target_id]

        if kind:
            results = [r for r in results if r.kind == kind]

        results = [r for r in results if min_strength <= r.strength <= max_strength]

        if bidirectional_only:
            results = [r for r in results if r.bidirectional]

        return sorted(results, key=lambda r: r.strength, reverse=True)

    def find_mutual(self, entity_a: str, entity_b: str) -> list[Relationship]:
        """Find all relationships between two entities (both directions)."""
        a_to_b = self.graph.relationships_between(entity_a, entity_b)
        b_to_a = self.graph.relationships_between(entity_b, entity_a)
        return a_to_b + b_to_a

    def find_strongest(
        self,
        entity_id: str,
        kind: RelationshipKind | None = None,
        min_strength: float = 0.0,
    ) -> Relationship | None:
        """Find the strongest relationship for an entity."""
        rels = self.graph.relationships_for(entity_id, kind=kind, min_strength=min_strength)
        return rels[0] if rels else None

    def find_all_kinds(self, entity_id: str) -> dict[str, list[Relationship]]:
        """Group relationships by kind for an entity."""
        rels = self.graph.relationships_for(entity_id)
        by_kind: dict[str, list[Relationship]] = {}
        for rel in rels:
            by_kind.setdefault(rel.kind.value, []).append(rel)
        return by_kind

    # ── Aggregation ─────────────────────────────────────────────

    def count_by_kind(self) -> dict[str, int]:
        """Count relationships by kind."""
        counts: dict[str, int] = {}
        for rel in self.graph.all_relationships():  # DIP: Use public API
            counts[rel.kind.value] = counts.get(rel.kind.value, 0) + 1
        return counts

    def average_strength(self, kind: RelationshipKind | None = None) -> float:
        """Average strength of relationships."""
        rels = self.graph.all_relationships()  # DIP: Use public API
        if kind:
            rels = [r for r in rels if r.kind == kind]
        if not rels:
            return 0.0
        return sum(r.strength for r in rels) / len(rels)

    def strength_distribution(self) -> dict[str, int]:
        """Distribution of relationship strengths."""
        dist = {"strong": 0, "medium": 0, "weak": 0, "minimal": 0}
        for rel in self.graph.all_relationships():  # DIP: Use public API
            bucket = self._strength_bucket(rel.strength)
            dist[bucket] += 1
        return dist

    def most_connected(self, limit: int = 10) -> list[tuple[str, int]]:
        """Find the most connected entities."""
        connection_counts: dict[str, int] = {}
        for rel in self.graph.all_relationships():  # DIP: Use public API
            connection_counts[rel.source_id] = connection_counts.get(rel.source_id, 0) + 1
            connection_counts[rel.target_id] = connection_counts.get(rel.target_id, 0) + 1
        return sorted(connection_counts.items(), key=lambda x: x[1], reverse=True)[:limit]

    # ── Bulk Operations ─────────────────────────────────────────

    def add_bulk(self, relationships: list[dict[str, Any]]) -> list[Relationship]:
        """Add multiple relationships at once."""
        results = []
        for rel_data in relationships:
            rel = self.add(
                source_id=rel_data["source_id"],
                target_id=rel_data["target_id"],
                kind=rel_data.get("kind", RelationshipKind.RELATED_TO),
                strength=rel_data.get("strength", 0.5),
                **{k: v for k, v in rel_data.items() if k not in ("source_id", "target_id", "kind", "strength")},
            )
            results.append(rel)
        return results

    def remove_by_entity(self, entity_id: str) -> int:
        """Remove all relationships involving an entity."""
        rels = self.graph.relationships_for(entity_id)
        count = 0
        for rel in rels:
            if self.graph.remove(rel.relationship_id):
                count += 1
                # Clean up indexes
                self._kind_index.get(rel.kind.value, set()).discard(rel.relationship_id)
                bucket = self._strength_bucket(rel.strength)
                self._strength_buckets.get(bucket, set()).discard(rel.relationship_id)
        return count

    # ── Analytics ───────────────────────────────────────────────

    def analytics(self) -> dict[str, Any]:
        """Comprehensive analytics about the relationship graph."""
        stats = self.graph.stats()
        return {
            **stats,
            "count_by_kind": self.count_by_kind(),
            "average_strength": self.average_strength(),
            "strength_distribution": self.strength_distribution(),
            "top_connected": self.most_connected(5),
        }

    # ── Serialization ───────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize store to dict."""
        return self.graph.to_dict()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelationshipStore:
        """Deserialize store from dict."""
        graph = RelationshipGraph.from_dict(data)
        store = cls(graph=graph)
        # Rebuild indexes
        for rel in graph._relationships.values():
            kind_key = rel.kind.value
            store._kind_index.setdefault(kind_key, set()).add(rel.relationship_id)
            bucket = store._strength_bucket(rel.strength)
            store._strength_buckets.setdefault(bucket, set()).add(rel.relationship_id)
        return store
