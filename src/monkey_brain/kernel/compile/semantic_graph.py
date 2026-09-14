"""SemanticGraph — typed entity/relationship graph layer for the World Model.

Complements SparseTransitionTensor by providing:
    - Typed entities (Person, Car, Road, Enterprise, Government, ...)
    - Typed relationships (DRIVES, LOCATED_ON, WORKS_FOR, REGULATES, ...)
    - Traversal APIs (neighbors, path-finding by relationship type)
    - Ontology (entity hierarchy, inheritance)
    - Neo4j persistence

Architecture:
    SemanticGraph (knowledge layer)
        ↓ queries, traversals, ontology reasoning
    SparseTransitionTensor (computation layer)
        ↓ Bellman updates, Φ compilation, policy propagation
    Actors

The SemanticGraph answers: "What entities exist? What are their types?
What relationships connect them? What constraints apply?"

The SparseTransitionTensor answers: "Given state S, what's the best action?
What's the predicted reward? How does the policy propagate?"

Both layers are kept in sync:
    - Context Stream updates → SemanticGraph → Tensor (transition extraction)
    - Entity/relationship changes → Neo4j → In-memory cache

Neo4j schema:
    Nodes: (:Entity {id, entity_type, domain, name, attributes, state})
    Edges: (:Entity)-[:RELATES_TO {relation_type, domain, attributes}]->(:Entity)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

from src.monkey_brain.kernel.compile.entity import (
    Entity,
    EntityType,
    RelationType,
    EntityRegistry,
    Relationship,
)

logger = logging.getLogger("agentos.semantic_graph")


class SemanticGraph:
    """Typed entity/relationship graph backed by Neo4j + in-memory cache.

    Provides:
        - Entity CRUD (add, get, update, remove)
        - Relationship CRUD (relate, get_relationships, remove_relationship)
        - Traversal (neighbors, neighbors_by_type, find_path, shortest_path)
        - Ontology (entity_type_hierarchy, is_instance_of)
        - Sync with SparseTransitionTensor
        - Context Stream integration

    In-memory cache:
        - _entities: dict[str, Entity] — entity lookup by ID
        - _relationships: dict[str, list[Relationship]] — relationships from each entity
        - _adjacency: dict[str, dict[str, list[Relationship]]] — adjacency list by relation type

    Neo4j schema:
        - Nodes: (:Entity {id, entity_type, domain, name, ...})
        - Edges: (:Entity)-[:RELATES_TO {relation_type, ...}]->(:Entity)
    """

    def __init__(
        self,
        neo4j_uri: str | None = None,
        neo4j_user: str | None = None,
        neo4j_password: str | None = None,
    ) -> None:
        self._neo4j_uri = neo4j_uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self._neo4j_user = neo4j_user or os.getenv("NEO4J_USER", "neo4j")
        self._neo4j_password = neo4j_password or os.getenv("NEO4J_PASSWORD") or ""
        self._driver: Any = None

        # In-memory cache
        self._entities: dict[str, Entity] = {}
        self._relationships: dict[str, list[Relationship]] = {}  # entity_id → outgoing relationships
        self._adjacency: dict[str, dict[str, list[Relationship]]] = {}  # entity_id → {relation_type → [rels]}

        # Entity registry
        self._registry = EntityRegistry()

        # Pending async writes (flushed on next connect or explicit flush)
        self._pending_writes: list[tuple[str, Any]] = []  # (operation, data)

        # Context Stream callback
        self._on_entity_change: Callable[[str, Entity], None] | None = None
        self._on_relationship_change: Callable[[str, Relationship], None] | None = None

    # ── Async write scheduling ──────────────────────────────────────────

    def _schedule_write(self, operation: str, data: Any) -> None:
        """Queue a Neo4j write for later async execution.

        Safe to call from sync contexts. Writes are flushed when an
        event loop is available, or accumulated for batch flush.
        """
        try:
            import asyncio

            loop = asyncio.get_running_loop()
            # We're inside an async context — schedule directly
            if operation == "persist_entity":
                loop.create_task(self._persist_entity(data))
            elif operation == "persist_relationship":
                loop.create_task(self._persist_relationship(data))
            elif operation == "remove_entity":
                loop.create_task(self._remove_entity_from_neo4j(data))
            elif operation == "remove_relationship":
                src, dst, rel_type = data
                loop.create_task(self._remove_relationship_from_neo4j(src, dst, rel_type))
        except RuntimeError:
            # No running event loop — queue for later
            self._pending_writes.append((operation, data))

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Connect to Neo4j and create schema."""
        if not self._neo4j_password:
            logger.warning("SemanticGraph: Neo4j password unset, running in-memory only")
            return
        try:
            from neo4j import AsyncGraphDatabase

            self._driver = AsyncGraphDatabase.driver(
                self._neo4j_uri,
                auth=(self._neo4j_user, self._neo4j_password),
            )
            await self._create_schema()
            await self._load_from_neo4j()
            logger.info("SemanticGraph connected to %s", self._neo4j_uri)
        except ImportError:
            logger.warning("neo4j driver not installed — SemanticGraph running in-memory only")
        except Exception as exc:
            logger.warning("SemanticGraph connect failed: %s", exc)

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None

    def is_connected(self) -> bool:
        return self._driver is not None

    async def _create_schema(self) -> None:
        """Create Neo4j indexes and constraints."""
        if not self.is_connected():
            return
        async with self._driver.session() as session:
            await session.run("CREATE INDEX entity_id IF NOT EXISTS FOR (e:Entity) ON (e.id)")
            await session.run("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.entity_type)")
            await session.run("CREATE INDEX entity_domain IF NOT EXISTS FOR (e:Entity) ON (e.domain)")

    async def _load_from_neo4j(self) -> None:
        """Load all entities and relationships from Neo4j into memory."""
        if not self.is_connected():
            return
        async with self._driver.session() as session:
            # Load entities
            result = await session.run("MATCH (e:Entity) RETURN e")
            async for record in result:
                e = record["e"]
                entity = Entity(
                    id=e["id"],
                    entity_type=EntityType(e.get("entity_type", "entity")),
                    domain=e.get("domain", "default"),
                    attributes=json.loads(e.get("attributes", "{}")),
                    state=json.loads(e.get("state", "{}")),
                    tenant_id=e.get("tenant_id"),
                )
                self._entities[entity.id] = entity
                self._registry.register(entity)

            # Load relationships
            result = await session.run("MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) RETURN a.id as src, b.id as dst, r")
            async for record in result:
                r = record["r"]
                rel = Relationship(
                    id=f"{record['src']}_{r.get('relation_type', 'related_to')}_{record['dst']}",
                    source_id=record["src"],
                    target_id=record["dst"],
                    relation_type=RelationType(r.get("relation_type", "related_to")),
                    domain=r.get("domain", "default"),
                    attributes=json.loads(r.get("attributes", "{}")),
                )
                self._add_rel_to_cache(rel)

        logger.info(
            "SemanticGraph loaded %d entities, %d relationships from Neo4j",
            len(self._entities),
            sum(len(v) for v in self._relationships.values()),
        )

    # ── Entity CRUD ──────────────────────────────────────────────────────

    def add_entity(self, entity: Entity) -> Entity:
        """Add entity to graph (memory + Neo4j)."""
        existing = self._entities.get(entity.id)
        if existing is not None:
            return existing

        self._entities[entity.id] = entity
        self._registry.register(entity)
        self._relationships.setdefault(entity.id, [])
        self._adjacency.setdefault(entity.id, {})

        # Persist to Neo4j (fire-and-forget)
        if self.is_connected():
            self._schedule_write("persist_entity", entity)

        if self._on_entity_change:
            self._on_entity_change("add", entity)

        return entity

    def get_entity(self, entity_id: str) -> Entity | None:
        """Get entity by ID."""
        return self._entities.get(entity_id)

    def get_entities_by_type(self, entity_type: EntityType) -> list[Entity]:
        """Get all entities of a given type."""
        return [e for e in self._entities.values() if e.entity_type == entity_type]

    def get_entities_by_domain(self, domain: str) -> list[Entity]:
        """Get all entities in a domain."""
        return [e for e in self._entities.values() if e.domain == domain]

    def update_entity(self, entity_id: str, **kwargs: Any) -> Entity | None:
        """Update entity attributes/state."""
        entity = self._entities.get(entity_id)
        if entity is None:
            return None
        if "attributes" in kwargs:
            entity.attributes.update(kwargs["attributes"])
        if "state" in kwargs:
            entity.state.update(kwargs["state"])
        entity.updated_at = time.time()

        if self.is_connected():
            self._schedule_write("persist_entity", entity)

        if self._on_entity_change:
            self._on_entity_change("update", entity)

        return entity

    def remove_entity(self, entity_id: str) -> bool:
        """Remove entity and all its relationships."""
        entity = self._entities.pop(entity_id, None)
        if entity is None:
            return False

        # Remove all relationships involving this entity
        rels = self._relationships.pop(entity_id, [])
        for rel in rels:
            target_rels = self._relationships.get(rel.target_id, [])
            self._relationships[rel.target_id] = [r for r in target_rels if r.source_id != entity_id]

        self._adjacency.pop(entity_id, {})

        if self.is_connected():
            self._schedule_write("remove_entity", entity_id)

        if self._on_entity_change:
            self._on_entity_change("remove", entity)

        return True

    # ── Relationship CRUD ────────────────────────────────────────────────

    def relate(
        self,
        source_id: str,
        target_id: str,
        relation_type: RelationType = RelationType.RELATED_TO,
        domain: str = "default",
        attributes: dict | None = None,
    ) -> Relationship | None:
        """Create relationship between two entities."""
        if source_id not in self._entities or target_id not in self._entities:
            logger.warning(
                "SemanticGraph.relate: entity not found (%s or %s)",
                source_id,
                target_id,
            )
            return None

        rel = Relationship(
            id=f"{source_id}_{relation_type.value}_{target_id}",
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            domain=domain,
            attributes=attributes or {},
        )

        self._add_rel_to_cache(rel)

        if self.is_connected():
            self._schedule_write("persist_relationship", rel)

        if self._on_relationship_change:
            self._on_relationship_change("add", rel)

        return rel

    def get_relationships(
        self,
        entity_id: str,
        relation_type: RelationType | None = None,
        direction: str = "outgoing",
    ) -> list[Relationship]:
        """Get relationships for an entity.

        direction: "outgoing", "incoming", or "both"
        """
        results = []

        if direction in ("outgoing", "both"):
            outgoing = self._relationships.get(entity_id, [])
            if relation_type:
                outgoing = [r for r in outgoing if r.relation_type == relation_type]
            results.extend(outgoing)

        if direction in ("incoming", "both"):
            for other_id, rels in self._relationships.items():
                for r in rels:
                    if r.target_id == entity_id:
                        if relation_type is None or r.relation_type == relation_type:
                            results.append(r)

        return results

    def remove_relationship(self, source_id: str, target_id: str, relation_type: RelationType | None = None) -> int:
        """Remove relationships between two entities. Returns count removed."""
        removed = 0
        rels = self._relationships.get(source_id, [])
        remaining = []
        for r in rels:
            if r.target_id == target_id and (relation_type is None or r.relation_type == relation_type):
                removed += 1
                if self._on_relationship_change:
                    self._on_relationship_change("remove", r)
            else:
                remaining.append(r)
        self._relationships[source_id] = remaining

        if self.is_connected() and removed > 0:
            self._schedule_write("remove_relationship", (source_id, target_id, relation_type))

        return removed

    def _add_rel_to_cache(self, rel: Relationship) -> None:
        """Add relationship to in-memory cache."""
        self._relationships.setdefault(rel.source_id, []).append(rel)
        self._adjacency.setdefault(rel.source_id, {}).setdefault(rel.relation_type.value, []).append(rel)

    # ── Traversal APIs ───────────────────────────────────────────────────

    def neighbors(self, entity_id: str, max_depth: int = 1) -> list[Entity]:
        """Get neighboring entities up to max_depth hops."""
        visited = set()
        result = []
        frontier = [entity_id]

        for _ in range(max_depth):
            next_frontier = []
            for eid in frontier:
                if eid in visited:
                    continue
                visited.add(eid)
                for rel in self._relationships.get(eid, []):
                    if rel.target_id not in visited:
                        entity = self._entities.get(rel.target_id)
                        if entity:
                            result.append(entity)
                            next_frontier.append(rel.target_id)
            frontier = next_frontier

        return result

    def neighbors_by_type(self, entity_id: str, relation_type: RelationType, max_depth: int = 1) -> list[Entity]:
        """Get neighbors connected by a specific relationship type."""
        visited = set()
        result = []
        frontier = [entity_id]

        for _ in range(max_depth):
            next_frontier = []
            for eid in frontier:
                if eid in visited:
                    continue
                visited.add(eid)
                rels = self._adjacency.get(eid, {}).get(relation_type.value, [])
                for rel in rels:
                    if rel.target_id not in visited:
                        entity = self._entities.get(rel.target_id)
                        if entity:
                            result.append(entity)
                            next_frontier.append(rel.target_id)
            frontier = next_frontier

        return result

    def find_path(self, start_id: str, goal_id: str, max_depth: int = 6) -> list[Entity] | None:
        """BFS shortest path between two entities."""
        if start_id not in self._entities or goal_id not in self._entities:
            return None

        visited = {start_id}
        queue = [(start_id, [start_id])]

        while queue:
            current, path = queue.pop(0)
            if current == goal_id:
                return [self._entities[eid] for eid in path if eid in self._entities]

            if len(path) >= max_depth:
                continue

            for rel in self._relationships.get(current, []):
                if rel.target_id not in visited:
                    visited.add(rel.target_id)
                    queue.append((rel.target_id, path + [rel.target_id]))

        return None

    def find_path_by_types(
        self,
        start_id: str,
        goal_id: str,
        allowed_relation_types: list[RelationType],
        max_depth: int = 6,
    ) -> list[Entity] | None:
        """BFS path constrained by relationship types."""
        if start_id not in self._entities or goal_id not in self._entities:
            return None

        allowed = {rt.value for rt in allowed_relation_types}
        visited = {start_id}
        queue = [(start_id, [start_id])]

        while queue:
            current, path = queue.pop(0)
            if current == goal_id:
                return [self._entities[eid] for eid in path if eid in self._entities]

            if len(path) >= max_depth:
                continue

            for rel in self._relationships.get(current, []):
                if rel.relation_type.value in allowed and rel.target_id not in visited:
                    visited.add(rel.target_id)
                    queue.append((rel.target_id, path + [rel.target_id]))

        return None

    # ── Ontology ─────────────────────────────────────────────────────────

    def entity_type_hierarchy(self) -> dict[str, list[str]]:
        """Return entity type hierarchy."""
        return {
            "Entity": ["PassiveEntity", "Actor"],
            "PassiveEntity": [
                "Product",
                "Machine",
                "Building",
                "Road",
                "Sensor",
                "Device",
                "Document",
                "Organization",
                "Location",
                "DigitalEntity",
            ],
            "Actor": [
                "Person",
                "Vehicle",
                "Robot",
                "Drone",
                "DigitalAgent",
                "AutonomousSystem",
            ],
            "Organization": [
                "Enterprise",
                "Government",
                "Community",
                "NGO",
                "Institution",
            ],
            "Location": [
                "Country",
                "StateProvince",
                "City",
                "Factory",
                "Warehouse",
                "Store",
                "Port",
            ],
            "DigitalEntity": ["Service", "API", "DigitalTwin"],
            "Vehicle": ["Car", "Truck"],
        }

    def is_instance_of(self, entity_id: str, entity_type: EntityType) -> bool:
        """Check if entity is an instance of (or subclass of) a type."""
        entity = self._entities.get(entity_id)
        if entity is None:
            return False
        return entity.entity_type == entity_type

    def instances_of(self, entity_type: EntityType) -> list[Entity]:
        """Get all instances of a specific entity type."""
        return self.get_entities_by_type(entity_type)

    # ── Sync with SparseTransitionTensor ─────────────────────────────────

    def populate_tensor(self, tensor: Any) -> int:
        """Extract transitions from relationships and add to tensor.

        For each (Entity)-[:RELATES_TO]->(Entity) relationship,
        adds a transition (source_id, target_id) to the tensor.

        Returns number of transitions added.
        """
        count = 0
        for entity_id, rels in self._relationships.items():
            for rel in rels:
                tensor.observe(
                    rel.source_id,
                    rel.target_id,
                    domain=rel.domain,
                    weight=1.0,
                )
                count += 1
        return count

    def sync_from_tensor(self, tensor: Any) -> int:
        """Extract entities and transitions from tensor and add to graph.

        For each (src, dst) in the tensor, creates entities if missing
        and adds a RELATED_TO relationship.

        Returns number of relationships added.
        """
        count = 0
        for src, dst in tensor:
            # Create entities if missing
            if src not in self._entities:
                self.add_entity(Entity(id=src, entity_type=EntityType.ENTITY))
            if dst not in self._entities:
                self.add_entity(Entity(id=dst, entity_type=EntityType.ENTITY))

            # Check if relationship already exists
            existing = self.get_relationships(src, direction="outgoing")
            if not any(r.target_id == dst for r in existing):
                self.relate(
                    src,
                    dst,
                    RelationType.TRANSITIONS_TO,
                    domain=(tensor.domain_of(src) if hasattr(tensor, "domain_of") else "default"),
                )
                count += 1

        return count

    # ── Context Stream integration ───────────────────────────────────────

    def on_context_event(self, event: Any) -> None:
        """Handle Context Stream events — update graph from world mutations."""
        if not hasattr(event, "entity") or not hasattr(event, "attribute"):
            return

        src = event.entity
        dst = event.attribute

        # Ensure entities exist
        if src not in self._entities:
            self.add_entity(Entity(id=src, entity_type=EntityType.ENTITY))
        if dst not in self._entities:
            self.add_entity(Entity(id=dst, entity_type=EntityType.ENTITY))

        # Add transition relationship
        existing = self.get_relationships(src, RelationType.TRANSITIONS_TO, "outgoing")
        if not any(r.target_id == dst for r in existing):
            domain = event.metadata.get("domain", "default") if hasattr(event, "metadata") else "default"
            self.relate(src, dst, RelationType.TRANSITIONS_TO, domain=domain)

    # ── Neo4j persistence (async, fire-and-forget) ───────────────────────

    async def _persist_entity(self, entity: Entity) -> None:
        if not self.is_connected():
            return
        try:
            async with self._driver.session() as session:
                await session.run(
                    "MERGE (e:Entity {id: $id}) "
                    "SET e.entity_type = $entity_type, e.domain = $domain, "
                    "    e.name = $name, e.attributes = $attributes, "
                    "    e.state = $state, e.tenant_id = $tenant_id, "
                    "    e.updated_at = $updated_at",
                    id=entity.id,
                    entity_type=entity.entity_type.value,
                    domain=entity.domain,
                    name=entity.attributes.get("name", entity.id),
                    attributes=json.dumps(entity.attributes),
                    state=json.dumps(entity.state),
                    tenant_id=entity.tenant_id or "",
                    updated_at=entity.updated_at,
                )
        except Exception as exc:
            logger.debug("SemanticGraph: persist entity failed: %s", exc)

    async def _persist_relationship(self, rel: Relationship) -> None:
        if not self.is_connected():
            return
        try:
            async with self._driver.session() as session:
                await session.run(
                    "MATCH (a:Entity {id: $src}), (b:Entity {id: $dst}) "
                    "MERGE (a)-[r:RELATES_TO {relation_type: $rel_type}]->(b) "
                    "SET r.domain = $domain, r.attributes = $attributes, "
                    "    r.is_active = true, r.updated_at = $updated_at",
                    src=rel.source_id,
                    dst=rel.target_id,
                    rel_type=rel.relation_type.value,
                    domain=rel.domain,
                    attributes=json.dumps(rel.attributes),
                    updated_at=rel.updated_at,
                )
        except Exception as exc:
            logger.debug("SemanticGraph: persist relationship failed: %s", exc)

    async def _remove_entity_from_neo4j(self, entity_id: str) -> None:
        if not self.is_connected():
            return
        try:
            async with self._driver.session() as session:
                await session.run(
                    "MATCH (e:Entity {id: $id}) DETACH DELETE e",
                    id=entity_id,
                )
        except Exception as exc:
            logger.debug("SemanticGraph: remove entity failed: %s", exc)

    async def _remove_relationship_from_neo4j(self, src: str, dst: str, rel_type: RelationType | None) -> None:
        if not self.is_connected():
            return
        try:
            async with self._driver.session() as session:
                if rel_type:
                    await session.run(
                        "MATCH (a:Entity {id: $src})-[r:RELATES_TO]->(b:Entity {id: $dst}) "
                        "WHERE r.relation_type = $rel_type DELETE r",
                        src=src,
                        dst=dst,
                        rel_type=rel_type.value,
                    )
                else:
                    await session.run(
                        "MATCH (a:Entity {id: $src})-[r:RELATES_TO]->(b:Entity {id: $dst}) DELETE r",
                        src=src,
                        dst=dst,
                    )
        except Exception as exc:
            logger.debug("SemanticGraph: remove relationship failed: %s", exc)

    # ── Introspection ────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "entities": len(self._entities),
            "relationships": sum(len(v) for v in self._relationships.values()),
            "entity_types": len(set(e.entity_type.value for e in self._entities.values())),
            "domains": list(set(e.domain for e in self._entities.values())),
            "neo4j_connected": self.is_connected(),
        }

    def __len__(self) -> int:
        return len(self._entities)

    def __contains__(self, entity_id: str) -> bool:
        return entity_id in self._entities
