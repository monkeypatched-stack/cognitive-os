"""Shared Semantic World Model (Step 12.2) — replaces isolated actor world
views with a shared semantic cognitive world.

Actors no longer own the world. Actors observe it.

The SharedWorld contains:
    Entities, Relationships, Events, Resources, Capabilities,
    Locations, Policies, Time.

Supports:
    versioning, provenance, confidence, partial knowledge.

This is the semantic layer on top of GlobalWorld's tensor-based model,
providing typed, queryable world state that multiple actors can observe
through different lenses.

Serialization contract:
    Every type provides to_dict() -> dict and from_dict(d) -> Self.
    SharedWorld provides to_dict() for full snapshot serialization
    and from_dict(d) for reconstruction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

# ═══════════════════════════════════════════════════════════════════════════
# World Entity Types
# ═══════════════════════════════════════════════════════════════════════════


class WorldEntityType(Enum):
    ENTITY = "entity"
    RESOURCE = "resource"
    CAPABILITY = "capability"
    LOCATION = "location"
    POLICY = "policy"
    EVENT = "event"


class RelationshipKind(Enum):
    IS_A = "is_a"
    HAS = "has"
    LOCATED_IN = "located_in"
    DEPENDS_ON = "depends_on"
    PRODUCES = "produces"
    CONSUMES = "consumes"
    GOVERNS = "governs"
    RELATED_TO = "related_to"


class EventType(Enum):
    OBSERVATION = "observation"
    ACTION = "action"
    STATE_CHANGE = "state_change"
    INTERACTION = "interaction"
    WORLD_UPDATE = "world_update"


def _ts(t: float) -> float:
    return round(t, 6)


# ═══════════════════════════════════════════════════════════════════════════
# Core World Types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class WorldEntity:
    """An entity in the shared semantic world."""

    entity_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    entity_type: WorldEntityType = WorldEntityType.ENTITY
    attributes: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    """How confident the world model is about this entity's state."""
    provenance: str = ""
    """Where this entity's information came from."""
    version: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    owner_society_id: str = ""
    """Empty (the default) means globally visible to every actor, exactly
    the current behavior. Set to a society_id to restrict observation
    (kernel/society/observation.py::ObservationProvider) to actors with an
    active Membership in that society — e.g. a household's shared pantry
    should disappear from a member's observation once they leave."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "entity_type": self.entity_type.value,
            "attributes": dict(self.attributes),
            "state": dict(self.state),
            "confidence": self.confidence,
            "provenance": self.provenance,
            "version": self.version,
            "created_at": _ts(self.created_at),
            "updated_at": _ts(self.updated_at),
            "owner_society_id": self.owner_society_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorldEntity:
        return cls(
            entity_id=d.get("entity_id", uuid4().hex),
            name=d.get("name", ""),
            entity_type=(WorldEntityType(d["entity_type"]) if "entity_type" in d else WorldEntityType.ENTITY),
            attributes=dict(d.get("attributes", {})),
            state=dict(d.get("state", {})),
            confidence=d.get("confidence", 1.0),
            provenance=d.get("provenance", ""),
            version=d.get("version", 0),
            created_at=d.get("created_at", 0.0),
            updated_at=d.get("updated_at", 0.0),
            owner_society_id=d.get("owner_society_id", ""),
        )


@dataclass(frozen=True)
class WorldRelationship:
    """A typed relationship between two world entities."""

    relationship_id: str = field(default_factory=lambda: uuid4().hex)
    source_id: str = ""
    target_id: str = ""
    kind: RelationshipKind = RelationshipKind.RELATED_TO
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    version: int = 0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind.value,
            "attributes": dict(self.attributes),
            "confidence": self.confidence,
            "version": self.version,
            "created_at": _ts(self.created_at),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorldRelationship:
        return cls(
            relationship_id=d.get("relationship_id", uuid4().hex),
            source_id=d.get("source_id", ""),
            target_id=d.get("target_id", ""),
            kind=(RelationshipKind(d["kind"]) if "kind" in d else RelationshipKind.RELATED_TO),
            attributes=dict(d.get("attributes", {})),
            confidence=d.get("confidence", 1.0),
            version=d.get("version", 0),
            created_at=d.get("created_at", 0.0),
        )


@dataclass(frozen=True)
class WorldEvent:
    """An event that occurred in the shared world."""

    event_id: str = field(default_factory=lambda: uuid4().hex)
    event_type: EventType = EventType.STATE_CHANGE
    entity_id: str = ""
    description: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    source_actor_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "entity_id": self.entity_id,
            "description": self.description,
            "attributes": dict(self.attributes),
            "confidence": self.confidence,
            "source_actor_id": self.source_actor_id,
            "timestamp": _ts(self.timestamp),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorldEvent:
        return cls(
            event_id=d.get("event_id", uuid4().hex),
            event_type=(EventType(d["event_type"]) if "event_type" in d else EventType.STATE_CHANGE),
            entity_id=d.get("entity_id", ""),
            description=d.get("description", ""),
            attributes=dict(d.get("attributes", {})),
            confidence=d.get("confidence", 1.0),
            source_actor_id=d.get("source_actor_id", ""),
            timestamp=d.get("timestamp", 0.0),
        )


@dataclass(frozen=True)
class WorldResource:
    """A resource in the shared world (food, energy, money, time, etc.)."""

    resource_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    resource_type: str = ""
    quantity: float = 0.0
    unit: str = ""
    location_id: str = ""
    owner_id: str = ""
    confidence: float = 1.0
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "name": self.name,
            "resource_type": self.resource_type,
            "quantity": self.quantity,
            "unit": self.unit,
            "location_id": self.location_id,
            "owner_id": self.owner_id,
            "confidence": self.confidence,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorldResource:
        return cls(
            resource_id=d.get("resource_id", uuid4().hex),
            name=d.get("name", ""),
            resource_type=d.get("resource_type", ""),
            quantity=d.get("quantity", 0.0),
            unit=d.get("unit", ""),
            location_id=d.get("location_id", ""),
            owner_id=d.get("owner_id", ""),
            confidence=d.get("confidence", 1.0),
            version=d.get("version", 0),
        )


@dataclass(frozen=True)
class WorldCapability:
    """A capability available in the world (not tied to a specific actor)."""

    capability_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    description: str = ""
    requirements: tuple[str, ...] = ()
    confidence: float = 1.0
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "description": self.description,
            "requirements": list(self.requirements),
            "confidence": self.confidence,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorldCapability:
        return cls(
            capability_id=d.get("capability_id", uuid4().hex),
            name=d.get("name", ""),
            description=d.get("description", ""),
            requirements=tuple(d.get("requirements", [])),
            confidence=d.get("confidence", 1.0),
            version=d.get("version", 0),
        )


@dataclass(frozen=True)
class WorldLocation:
    """A location in the shared world."""

    location_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    address: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "location_id": self.location_id,
            "name": self.name,
            "address": self.address,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "attributes": dict(self.attributes),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorldLocation:
        return cls(
            location_id=d.get("location_id", uuid4().hex),
            name=d.get("name", ""),
            address=d.get("address", ""),
            latitude=d.get("latitude", 0.0),
            longitude=d.get("longitude", 0.0),
            attributes=dict(d.get("attributes", {})),
            version=d.get("version", 0),
        )


@dataclass(frozen=True)
class WorldPolicy:
    """A governance policy in the shared world."""

    policy_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    description: str = ""
    rules: tuple[str, ...] = ()
    scope: str = ""
    """e.g. 'global', 'actor_type:<type>', 'actor_id:<id>'."""
    confidence: float = 1.0
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "description": self.description,
            "rules": list(self.rules),
            "scope": self.scope,
            "confidence": self.confidence,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorldPolicy:
        return cls(
            policy_id=d.get("policy_id", uuid4().hex),
            name=d.get("name", ""),
            description=d.get("description", ""),
            rules=tuple(d.get("rules", [])),
            scope=d.get("scope", ""),
            confidence=d.get("confidence", 1.0),
            version=d.get("version", 0),
        )


@dataclass(frozen=True)
class WorldVersion:
    """A snapshot of the world at a point in time for versioning."""

    version_id: str = field(default_factory=lambda: uuid4().hex)
    version: int = 0
    timestamp: float = field(default_factory=time.time)
    entity_count: int = 0
    relationship_count: int = 0
    event_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "version": self.version,
            "timestamp": _ts(self.timestamp),
            "entity_count": self.entity_count,
            "relationship_count": self.relationship_count,
            "event_count": self.event_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorldVersion:
        return cls(
            version_id=d.get("version_id", uuid4().hex),
            version=d.get("version", 0),
            timestamp=d.get("timestamp", 0.0),
            entity_count=d.get("entity_count", 0),
            relationship_count=d.get("relationship_count", 0),
            event_count=d.get("event_count", 0),
        )


# ═══════════════════════════════════════════════════════════════════════════
# SharedWorld
# ═══════════════════════════════════════════════════════════════════════════


class SharedWorld:
    """The shared semantic world that all actors observe.

    Actors no longer own the world — they observe it.
    The world is versioned and supports provenance tracking.

    Immutability: entities/relationships are frozen dataclasses.
    Updates produce new versions (append-only history).
    """

    def __init__(self, max_events: int = 10000) -> None:
        self._entities: dict[str, WorldEntity] = {}
        self._relationships: dict[str, WorldRelationship] = {}
        self._events: list[WorldEvent] = []
        self._max_events = max_events
        self._resources: dict[str, WorldResource] = {}
        self._capabilities: dict[str, WorldCapability] = {}
        self._locations: dict[str, WorldLocation] = {}
        self._policies: dict[str, WorldPolicy] = {}
        self._version = 0
        self._version_history: list[WorldVersion] = []

    @property
    def version(self) -> int:
        return self._version

    def snapshot(self) -> WorldVersion:
        return WorldVersion(
            version=self._version,
            entity_count=len(self._entities),
            relationship_count=len(self._relationships),
            event_count=len(self._events),
        )

    def version_history(self, limit: int = 100) -> tuple[WorldVersion, ...]:
        """Temporal history of world snapshots, one per mutation (Step 12.5:
        `_version_history` was declared but never populated — the world's
        required "temporal history" data-model item was silently missing)."""
        return tuple(self._version_history[-limit:])

    # ── Entity operations ────────────────────────────────────────────────

    def add_entity(self, entity: WorldEntity) -> None:
        self._require_write("shared_world.add_entity")
        self._entities[entity.entity_id] = entity
        self._bump_version()

    def get_entity(self, entity_id: str) -> WorldEntity | None:
        return self._entities.get(entity_id)

    def update_entity(
        self,
        entity_id: str,
        state: dict[str, Any] | None = None,
        owner_society_id: str | None = None,
        **attributes: Any,
    ) -> WorldEntity | None:
        self._require_write("shared_world.update_entity")
        old = self._entities.get(entity_id)
        if old is None:
            return None
        updated = WorldEntity(
            entity_id=old.entity_id,
            name=old.name,
            entity_type=old.entity_type,
            attributes={**old.attributes, **attributes},
            state={**old.state, **(state or {})},
            confidence=old.confidence,
            provenance=old.provenance,
            version=old.version + 1,
            created_at=old.created_at,
            updated_at=time.time(),
            owner_society_id=(old.owner_society_id if owner_society_id is None else owner_society_id),
        )
        self._entities[entity_id] = updated
        self._bump_version()
        return updated

    def remove_entity(self, entity_id: str) -> bool:
        self._require_write("shared_world.remove_entity")
        if entity_id in self._entities:
            del self._entities[entity_id]
            self._bump_version()
            return True
        return False

    def entities(self) -> tuple[WorldEntity, ...]:
        return tuple(self._entities.values())

    def entities_by_type(self, entity_type: WorldEntityType) -> tuple[WorldEntity, ...]:
        return tuple(e for e in self._entities.values() if e.entity_type == entity_type)

    # ── Relationship operations ──────────────────────────────────────────

    def add_relationship(self, relationship: WorldRelationship) -> None:
        self._require_write("shared_world.add_relationship")
        self._relationships[relationship.relationship_id] = relationship
        self._bump_version()

    def remove_relationship(self, relationship_id: str) -> bool:
        self._require_write("shared_world.remove_relationship")
        if relationship_id in self._relationships:
            del self._relationships[relationship_id]
            self._bump_version()
            return True
        return False

    def relationships(self) -> tuple[WorldRelationship, ...]:
        return tuple(self._relationships.values())

    def relationships_for(self, entity_id: str) -> tuple[WorldRelationship, ...]:
        return tuple(r for r in self._relationships.values() if r.source_id == entity_id or r.target_id == entity_id)

    # ── Event operations ─────────────────────────────────────────────────

    def record_event(self, event: WorldEvent) -> None:
        self._require_write("shared_world.record_event")
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events :]
        self._bump_version()

    def remove_event(self, event_id: str) -> bool:
        self._require_write("shared_world.remove_event")
        for i, e in enumerate(self._events):
            if e.event_id == event_id:
                self._events.pop(i)
                self._bump_version()
                return True
        return False

    def events(self, *, event_type: EventType | None = None, limit: int = 100) -> tuple[WorldEvent, ...]:
        filtered = self._events if event_type is None else [e for e in self._events if e.event_type == event_type]
        return tuple(filtered[-limit:])

    @property
    def event_count(self) -> int:
        return len(self._events)

    def events_since(self, index: int, *, event_type: EventType | None = None) -> tuple[WorldEvent, ...]:
        """Every event recorded since a prior events_since()/event_count
        cursor position -- the reconciliation-pass counterpart to events()'s
        "last N" view. Used by PlanetaryRuntime._run_cycle to fold
        actor-published World Perturbation Events into entity state exactly
        once per cycle, without a separate version-tracking mechanism.
        (Cursor is a plain event-list index, not `version`, since version
        also increments on non-event mutations like add_entity/perturb.)
        """
        tail = self._events[index:]
        if event_type is not None:
            tail = [e for e in tail if e.event_type == event_type]
        return tuple(tail)

    # ── Resource operations ──────────────────────────────────────────────

    def add_resource(self, resource: WorldResource) -> None:
        self._require_write("shared_world.add_resource")
        self._resources[resource.resource_id] = resource
        self._bump_version()

    def get_resource(self, resource_id: str) -> WorldResource | None:
        return self._resources.get(resource_id)

    def update_resource(self, resource_id: str, **kwargs: Any) -> WorldResource | None:
        self._require_write("shared_world.update_resource")
        old = self._resources.get(resource_id)
        if old is None:
            return None
        updated = WorldResource(
            resource_id=old.resource_id,
            name=kwargs.get("name", old.name),
            resource_type=kwargs.get("resource_type", old.resource_type),
            quantity=kwargs.get("quantity", old.quantity),
            unit=kwargs.get("unit", old.unit),
            location_id=kwargs.get("location_id", old.location_id),
            owner_id=kwargs.get("owner_id", old.owner_id),
            confidence=old.confidence,
            version=old.version + 1,
        )
        self._resources[resource_id] = updated
        self._bump_version()
        return updated

    def remove_resource(self, resource_id: str) -> bool:
        self._require_write("shared_world.remove_resource")
        if resource_id in self._resources:
            del self._resources[resource_id]
            self._bump_version()
            return True
        return False

    def resources(self) -> tuple[WorldResource, ...]:
        return tuple(self._resources.values())

    # ── Capability operations ────────────────────────────────────────────

    def add_capability(self, capability: WorldCapability) -> None:
        self._require_write("shared_world.add_capability")
        self._capabilities[capability.capability_id] = capability
        self._bump_version()

    def record_capability(
        self,
        *,
        capability_id: str = "",
        name: str = "",
        description: str = "",
        requirements: tuple[str, ...] = (),
        confidence: float = 1.0,
    ) -> WorldCapability:
        """Register (or re-register) a named capability as a versioned
        world fact. CognitiveOS Constitution: "knowledge, policies and
        capabilities are versioned infrastructure" -- WorldCapability.version
        was declared but add_capability() had zero real callers anywhere in
        the codebase, so the field never carried a live value. Re-recording
        the same capability_id bumps version instead of silently
        overwriting it back to 0, so the version genuinely tracks how many
        times a capability's world-facing description has changed."""
        existing = self._capabilities.get(capability_id) if capability_id else None
        capability = WorldCapability(
            capability_id=capability_id or uuid4().hex,
            name=name,
            description=description,
            requirements=requirements,
            confidence=confidence,
            version=(existing.version + 1) if existing is not None else 0,
        )
        self.add_capability(capability)
        return capability

    def capabilities(self) -> tuple[WorldCapability, ...]:
        return tuple(self._capabilities.values())

    # ── Location operations ──────────────────────────────────────────────

    def add_location(self, location: WorldLocation) -> None:
        self._require_write("shared_world.add_location")
        self._locations[location.location_id] = location
        self._bump_version()

    def get_location(self, location_id: str) -> WorldLocation | None:
        return self._locations.get(location_id)

    def update_location(self, location_id: str, **kwargs: Any) -> WorldLocation | None:
        self._require_write("shared_world.update_location")
        old = self._locations.get(location_id)
        if old is None:
            return None
        updated = WorldLocation(
            location_id=old.location_id,
            name=kwargs.get("name", old.name),
            address=kwargs.get("address", old.address),
            latitude=kwargs.get("latitude", old.latitude),
            longitude=kwargs.get("longitude", old.longitude),
            attributes={**old.attributes, **(kwargs.get("attributes") or {})},
            version=old.version + 1,
        )
        self._locations[location_id] = updated
        self._bump_version()
        return updated

    def remove_location(self, location_id: str) -> bool:
        self._require_write("shared_world.remove_location")
        if location_id in self._locations:
            del self._locations[location_id]
            self._bump_version()
            return True
        return False

    def locations(self) -> tuple[WorldLocation, ...]:
        return tuple(self._locations.values())

    # ── Policy operations ────────────────────────────────────────────────

    def add_policy(self, policy: WorldPolicy) -> None:
        self._require_write("shared_world.add_policy")
        self._policies[policy.policy_id] = policy
        self._bump_version()

    def record_policy(
        self,
        *,
        policy_id: str = "",
        name: str = "",
        description: str = "",
        rules: tuple[str, ...] = (),
        scope: str = "",
        confidence: float = 1.0,
    ) -> WorldPolicy:
        """Register (or re-register) a named policy as a versioned world
        fact -- same bridging purpose as record_capability() above, for
        SocietyGovernanceEngine.add_policy() (society/governance.py) and
        any other real policy registration path. Re-recording the same
        policy_id bumps version rather than resetting it."""
        existing = self._policies.get(policy_id) if policy_id else None
        policy = WorldPolicy(
            policy_id=policy_id or uuid4().hex,
            name=name,
            description=description,
            rules=rules,
            scope=scope,
            confidence=confidence,
            version=(existing.version + 1) if existing is not None else 0,
        )
        self.add_policy(policy)
        return policy

    def policies(self) -> tuple[WorldPolicy, ...]:
        return tuple(self._policies.values())

    # ── Query ────────────────────────────────────────────────────────────

    def query(self, *, entity_type: WorldEntityType | None = None, name_contains: str = "") -> tuple[WorldEntity, ...]:
        results = self._entities.values()
        if entity_type is not None:
            results = [e for e in results if e.entity_type == entity_type]
        if name_contains:
            nc = name_contains.lower()
            results = [e for e in results if nc in e.name.lower()]
        return tuple(results)

    # ── Serialization ────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self._version,
            "entities": [e.to_dict() for e in self._entities.values()],
            "relationships": [r.to_dict() for r in self._relationships.values()],
            "events": [e.to_dict() for e in self._events],
            "resources": [r.to_dict() for r in self._resources.values()],
            "capabilities": [c.to_dict() for c in self._capabilities.values()],
            "locations": [l.to_dict() for l in self._locations.values()],
            "policies": [p.to_dict() for p in self._policies.values()],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SharedWorld:
        world = cls()
        for ed in d.get("entities", []):
            world.add_entity(WorldEntity.from_dict(ed))
        for rd in d.get("relationships", []):
            world.add_relationship(WorldRelationship.from_dict(rd))
        for ed in d.get("events", []):
            world.record_event(WorldEvent.from_dict(ed))
        for rd in d.get("resources", []):
            world.add_resource(WorldResource.from_dict(rd))
        for cd in d.get("capabilities", []):
            world.add_capability(WorldCapability.from_dict(cd))
        for ld in d.get("locations", []):
            world.add_location(WorldLocation.from_dict(ld))
        for pd in d.get("policies", []):
            world.add_policy(WorldPolicy.from_dict(pd))
        return world

    # ── Internals ────────────────────────────────────────────────────────

    def perturb(self, magnitude: float = 0.1, event_chance: float = 0.3) -> list[dict[str, Any]]:
        """Stochastically mutate the world to simulate real-world dynamics.

        Each tick, numeric attributes drift by ±magnitude, and random
        world events may be spawned. Returns a list of perturbations
        applied (for logging/debugging).

        Called before each planetary cycle to ensure prediction ≠ execution.
        """
        self._require_write("shared_world.perturb")
        import random

        perturbations: list[dict[str, Any]] = []

        for entity in self._entities.values():
            for key, value in entity.attributes.items():
                if isinstance(value, (int, float)) and random.random() < 0.4:
                    noise = random.gauss(0, magnitude * abs(value) if value != 0 else magnitude)
                    new_value = round(type(value)(value + noise), 4)
                    perturbations.append(
                        {
                            "entity": entity.name,
                            "attribute": key,
                            "old": value,
                            "new": new_value,
                        }
                    )
                    updated = WorldEntity(
                        entity_id=entity.entity_id,
                        name=entity.name,
                        entity_type=entity.entity_type,
                        attributes={**entity.attributes, key: new_value},
                        state=entity.state,
                        confidence=max(0.1, entity.confidence - random.uniform(0, 0.05)),
                        provenance=entity.provenance,
                        version=entity.version + 1,
                        created_at=entity.created_at,
                        updated_at=time.time(),
                        owner_society_id=entity.owner_society_id,
                    )
                    self._entities[entity.entity_id] = updated

        if random.random() < event_chance:
            event_types = [
                "supply_disruption",
                "demand_shift",
                "equipment_failure",
                "quality_issue",
                "price_change",
                "capacity_change",
            ]
            etype = random.choice(event_types)
            event = WorldEvent(
                event_type=EventType.STATE_CHANGE,
                description=f"Stochastic event: {etype}",
                attributes={"severity": round(random.uniform(0.1, 0.9), 2)},
            )
            self.record_event(event)
            perturbations.append({"event": etype, "severity": event.attributes["severity"]})

        if perturbations:
            self._bump_version()
        return perturbations

    def _bump_version(self) -> None:
        self._version += 1
        self._version_history.append(self.snapshot())

    def _require_write(self, operation: str) -> None:
        from src.monkey_brain.kernel.security_boundary import (
            assert_state_mutation_allowed,
        )

        assert_state_mutation_allowed(operation)
