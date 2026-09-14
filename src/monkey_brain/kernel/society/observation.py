"""Multi-Actor Observation (Step 12.3) — allows multiple actors to observe
the same world differently.

Each actor receives:
    Shared World → ObservationProvider → Actor Observation

Support:
    partial observations, noisy observations, conflicting observations,
    delayed observations.

Do not merge beliefs yet — that's Step 12.4's job.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

from src.monkey_brain.kernel.society.world import (
    SharedWorld,
    WorldEntity,
    WorldRelationship,
    WorldEvent,
)


class ObservationQuality(Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NOISY = "noisy"
    DELAYED = "delayed"
    CONFLICTING = "conflicting"


@dataclass(frozen=True)
class ObservationFilter:
    """Controls what an actor sees of the shared world."""

    visible_entity_types: tuple[str, ...] = ()
    """Empty means all types visible."""
    visible_entity_ids: tuple[str, ...] = ()
    """Empty means all entities visible."""
    max_age_seconds: float = 0.0
    """0.0 means no age filter."""
    confidence_threshold: float = 0.0
    """Only include entities/relationships above this confidence."""
    noise_level: float = 0.0
    """0.0 = perfect observation, 1.0 = completely random."""
    partial_coverage: float = 1.0
    """1.0 = see everything, 0.5 = see half the world randomly."""


@dataclass(frozen=True)
class ObservedEntity:
    """An entity as seen by a specific actor through their observation lens."""

    entity: WorldEntity = field(default_factory=WorldEntity)
    quality: ObservationQuality = ObservationQuality.COMPLETE
    observation_delay: float = 0.0
    """Seconds between world state and this observation."""
    noise_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ObservedRelationship:
    """A relationship as seen by a specific actor."""

    relationship: WorldRelationship = field(default_factory=WorldRelationship)
    quality: ObservationQuality = ObservationQuality.COMPLETE


@dataclass(frozen=True)
class ActorObservation:
    """One actor's complete observation of the shared world at a point
    in time. Different actors may see different things."""

    observation_id: str = field(default_factory=lambda: uuid4().hex)
    actor_id: str = ""
    world_version: int = 0
    entities: tuple[ObservedEntity, ...] = ()
    relationships: tuple[ObservedRelationship, ...] = ()
    events: tuple[WorldEvent, ...] = ()
    quality: ObservationQuality = ObservationQuality.COMPLETE
    observation_time: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class ObservationProvider:
    """Generates per-actor observations from the shared world.

    Each actor gets a potentially different view of the world based on
    their ObservationFilter — partial knowledge, noise, delay, and
    conflicts are all first-class concerns.
    """

    def __init__(
        self,
        world: SharedWorld,
        membership_lookup: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._world = world
        self._membership_lookup = membership_lookup
        """(actor_id, society_id) -> bool, checked only for entities with a
        non-empty owner_society_id. None (the default — every existing
        standalone/unit-test construction) means no gating at all, so
        behavior is byte-for-byte unchanged unless a caller wires one in
        (PlanetaryRuntime._attach_society does, for the real API path)."""

    def observe(self, actor_id: str, filt: ObservationFilter | None = None) -> ActorObservation:
        filt = filt or ObservationFilter()
        entities = self._filter_entities(actor_id, filt)
        relationships = self._filter_relationships(filt, entities)
        events = self._filter_events(filt)
        quality = self._assess_quality(filt)

        return ActorObservation(
            actor_id=actor_id,
            world_version=self._world.version,
            entities=entities,
            relationships=relationships,
            events=events,
            quality=quality,
        )

    def observe_entity(
        self, actor_id: str, entity_id: str, filt: ObservationFilter | None = None
    ) -> ObservedEntity | None:
        filt = filt or ObservationFilter()
        entity = self._world.get_entity(entity_id)
        if entity is None:
            return None
        if not self._entity_visible(entity, filt, actor_id):
            return None
        return ObservedEntity(entity=entity, quality=self._assess_quality(filt))

    # ── Internals ────────────────────────────────────────────────────────

    def _filter_entities(self, actor_id: str, filt: ObservationFilter) -> tuple[ObservedEntity, ...]:
        result: list[ObservedEntity] = []
        for entity in self._world.entities():
            if not self._entity_visible(entity, filt, actor_id):
                continue
            quality = self._quality_for_entity(entity, filt)
            result.append(ObservedEntity(entity=entity, quality=quality))
        return tuple(result)

    def _entity_visible(self, entity: WorldEntity, filt: ObservationFilter, actor_id: str = "") -> bool:
        if filt.visible_entity_ids and entity.entity_id not in filt.visible_entity_ids:
            return False
        if filt.visible_entity_types and entity.entity_type.value not in filt.visible_entity_types:
            return False
        if filt.confidence_threshold > 0 and entity.confidence < filt.confidence_threshold:
            return False
        if filt.max_age_seconds > 0:
            age = time.time() - entity.updated_at
            if age > filt.max_age_seconds:
                return False
        if entity.owner_society_id and self._membership_lookup is not None:
            if not self._membership_lookup(actor_id, entity.owner_society_id):
                return False
        return True

    def _quality_for_entity(self, entity: WorldEntity, filt: ObservationFilter) -> ObservationQuality:
        if filt.noise_level > 0.5:
            return ObservationQuality.NOISY
        if filt.partial_coverage < 0.8:
            return ObservationQuality.PARTIAL
        return ObservationQuality.COMPLETE

    def _filter_relationships(
        self, filt: ObservationFilter, observed_entities: tuple[ObservedEntity, ...]
    ) -> tuple[ObservedRelationship, ...]:
        """Step 12.5 bugfix: this called `self._world.relationships_for("")`
        — an empty-string entity_id that never matches any real
        relationship (relationships_for() looks for an EXACT id match) —
        so observed relationships were always empty regardless of what the
        world actually contained. Fixed to iterate all world relationships
        (SharedWorld.relationships(), also added this step) and filter by
        the entities this observation already made visible."""
        visible_ids = {oe.entity.entity_id for oe in observed_entities}
        result: list[ObservedRelationship] = []
        for rel in self._world.relationships():
            if rel.source_id in visible_ids or rel.target_id in visible_ids:
                result.append(ObservedRelationship(relationship=rel))
        return tuple(result)

    def _filter_events(self, filt: ObservationFilter) -> tuple[WorldEvent, ...]:
        return self._world.events(limit=100)

    def _assess_quality(self, filt: ObservationFilter) -> ObservationQuality:
        if filt.noise_level > 0.5:
            return ObservationQuality.NOISY
        if filt.partial_coverage < 0.8:
            return ObservationQuality.PARTIAL
        return ObservationQuality.COMPLETE
