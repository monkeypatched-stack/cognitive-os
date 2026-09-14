"""GeographicRegistry — the sole mutating counterpart to the immutable
GeographicEntity hierarchy (entity.py). One generic manager for all 12
tiers (Universe -> Planet -> Region -> Country -> State -> County -> City
-> Street -> Building -> Floor -> Space -> Coordinate; Region/Floor
optional), not one near-duplicate per-tier manager per tier — mirrors the
immutable-dataclass-plus-manager pattern already used throughout
kernel/society/ (FederationManager, and this session's now-superseded
CountryManager/CityManager), generalized across every tier via
GeographicEntity's shared shape instead of repeating the same
add/remove-child logic per tier.

Enforces the two structural invariants the physical hierarchy needs:
  - single parent: add_child always detaches a child from any prior parent
    before attaching it to the new one (never duplicates).
  - strict tier adjacency: add_child rejects any parent/child pair that
    doesn't match entity.py::PARENT_TIER (e.g. a Building cannot be
    parented directly under a Country) — a linear, enforced tier order
    makes circular containment structurally impossible, not something to
    detect after the fact.

hosted_society_ids carries NO such restriction — a Society may be hosted at
any tier (Country, Building, Space, ...), which is the actual decoupling
mechanism the whole refactor is about.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from src.monkey_brain.kernel.geography.entity import (
    GeographicEntity,
    GeographicEntityType,
    PARENT_TIER,
    ROOT_ELIGIBLE,
    Universe,
    Planet,
    Region,
    Country,
    State,
    County,
    City,
    Street,
    Building,
    Floor,
    Space,
    Coordinate,
)

# entity_type -> the concrete dataclass to construct. Kept here, not in
# entity.py, so that this registry is the SOLE constructor of every tier
# (the conformance scanner's exclusive-constructor check for Planet/
# Country/.../Space) — callers (PlanetaryRuntime, API routes) go through
# create()/register() below instead of importing and instantiating the
# dataclasses themselves.
_ENTITY_CLASSES: dict[GeographicEntityType, type[GeographicEntity]] = {
    GeographicEntityType.UNIVERSE: Universe,
    GeographicEntityType.PLANET: Planet,
    GeographicEntityType.REGION: Region,
    GeographicEntityType.COUNTRY: Country,
    GeographicEntityType.STATE: State,
    GeographicEntityType.COUNTY: County,
    GeographicEntityType.CITY: City,
    GeographicEntityType.STREET: Street,
    GeographicEntityType.BUILDING: Building,
    GeographicEntityType.FLOOR: Floor,
    GeographicEntityType.SPACE: Space,
    GeographicEntityType.COORDINATE: Coordinate,
}


class GeographicRegistry:
    """Manages the entire physical-containment hierarchy, all tiers, one
    registry."""

    def __init__(self) -> None:
        self._entities: dict[str, GeographicEntity] = {}

    # ── CRUD ─────────────────────────────────────────────────────────────

    def register(self, entity: GeographicEntity) -> GeographicEntity:
        """Add a freshly-constructed entity (Planet/Country/.../Space
        instance) to the registry. Does not attach it to a parent — call
        add_child() separately, same two-step shape create_country()/
        add_city() already used in the superseded Country/City managers."""
        self._entities[entity.entity_id] = entity
        return entity

    def register_from_dict(self, d: dict[str, Any]) -> GeographicEntity | None:
        """Reconstruct a persisted entity (GeographicEntity.to_dict()'s
        shape) and register it directly, bypassing add_child()/create()'s
        parent-attachment step — persisted entities already carry correct
        parent_id/child_ids from when they were first created, so restoring
        them is a pure re-registration, not a re-derivation of the
        hierarchy. Picks the right concrete subclass (Planet/Country/.../
        Space) from entity_type so GeographicEntity.from_dict()'s `cls`
        carries the correct entity_type default — from_dict() itself never
        reads entity_type out of the dict, exactly like create() never
        constructs GeographicEntity directly. Returns None for an unknown
        entity_type, same failure shape as create()."""
        try:
            entity_type = GeographicEntityType(d.get("entity_type", ""))
        except ValueError:
            return None
        entity_cls = _ENTITY_CLASSES.get(entity_type)
        if entity_cls is None:
            return None
        return self.register(entity_cls.from_dict(d))

    def create(
        self,
        entity_type: GeographicEntityType,
        name: str,
        description: str = "",
        parent_id: str | None = None,
        **type_kwargs: Any,
    ) -> GeographicEntity | None:
        """Construct + register (+ attach to parent_id, if given) in one
        step — the sole entry point for creating a Planet/Country/.../Space
        instance; every caller (PlanetaryRuntime, API routes) should use
        this rather than importing the dataclasses directly.

        parent_id is required for every tier except PLANET (the root has no
        parent). Returns None if the entity_type is unknown, a required
        parent_id is missing, parent_id doesn't resolve, or the tier pairing
        is invalid (Building directly under Country, etc.) — in which case
        nothing is left behind in the registry."""
        entity_cls = _ENTITY_CLASSES.get(entity_type)
        if entity_cls is None:
            return None
        if entity_type not in ROOT_ELIGIBLE and not parent_id:
            return None
        entity = self.register(entity_cls(name=name, description=description, **type_kwargs))
        if parent_id is None:
            return entity
        if self.add_child(parent_id, entity.entity_id) is None:
            self.unregister(entity.entity_id)
            return None
        return self.get(entity.entity_id)

    def find_or_create(
        self,
        entity_type: GeographicEntityType,
        name: str,
        parent_id: str | None = None,
        description: str = "",
    ) -> GeographicEntity | None:
        """Real-world address ingestion (e.g. a geocoded street address)
        naturally re-covers the same Country/State/City repeatedly —
        "United States" should become one real Country entity, not a new
        one per address searched. Case-insensitive name match among the
        real siblings under parent_id (or among root-tier entities of
        entity_type when parent_id is None, e.g. reusing an existing
        Planet); creates via create() (so the same validation/tier-
        adjacency rules apply) only when nothing matches."""
        siblings = self.children_of(parent_id) if parent_id else self.all(entity_type)
        existing = next(
            (e for e in siblings if e.entity_type == entity_type and e.name.strip().lower() == name.strip().lower()),
            None,
        )
        if existing is not None:
            return existing
        return self.create(entity_type, name, description, parent_id)

    def get(self, entity_id: str) -> GeographicEntity | None:
        return self._entities.get(entity_id)

    def unregister(self, entity_id: str) -> None:
        """Remove an entity outright — used to undo a register() when a
        follow-up add_child() rejects the tier pairing, so a parentless,
        unreachable entity isn't left behind."""
        self._entities.pop(entity_id, None)

    def delete_subtree(self, entity_id: str) -> tuple[str, ...]:
        """Real deletion — entity_id and every descendant, recursively
        (a Building with Spaces under it must not leave orphaned Spaces
        whose parent_id points at nothing). Detaches from its own parent
        first so the parent's child_ids never references a removed id.
        Returns every entity_id actually removed (entity_id first, then
        descendants), or () if entity_id doesn't exist — callers (e.g.
        the API route) use an empty result to distinguish "not found"
        from "found, had no children."""
        entity = self._entities.get(entity_id)
        if entity is None:
            return ()
        if entity.parent_id is not None:
            self.remove_child(entity.parent_id, entity_id)
            entity = self._entities.get(entity_id, entity)

        removed: list[str] = []

        def _remove(current_id: str) -> None:
            current = self._entities.get(current_id)
            if current is None:
                return
            for child_id in current.child_ids:
                _remove(child_id)
            self._entities.pop(current_id, None)
            removed.append(current_id)

        _remove(entity_id)
        return tuple(removed)

    def all(self, entity_type: GeographicEntityType | None = None) -> tuple[GeographicEntity, ...]:
        if entity_type is None:
            return tuple(self._entities.values())
        return tuple(e for e in self._entities.values() if e.entity_type == entity_type)

    # ── Physical containment (strict single-parent, tier-adjacent) ──────

    def children_of(self, entity_id: str) -> tuple[GeographicEntity, ...]:
        entity = self._entities.get(entity_id)
        if entity is None:
            return ()
        return tuple(self._entities[cid] for cid in entity.child_ids if cid in self._entities)

    def parent_of(self, entity_id: str) -> GeographicEntity | None:
        entity = self._entities.get(entity_id)
        if entity is None or entity.parent_id is None:
            return None
        return self._entities.get(entity.parent_id)

    def add_child(self, parent_id: str, child_id: str) -> GeographicEntity | None:
        """Attach child_id under parent_id. Returns the updated PARENT, or
        None if either id doesn't exist or the tier pairing is invalid
        (e.g. Building under Country instead of Building under Street)."""
        parent = self._entities.get(parent_id)
        child = self._entities.get(child_id)
        if parent is None or child is None:
            return None
        allowed_parent_tiers = PARENT_TIER.get(child.entity_type)
        if allowed_parent_tiers is None or parent.entity_type not in allowed_parent_tiers:
            return None

        # Single parent: detach from any prior parent first.
        prior = self.parent_of(child_id)
        if prior is not None and prior.entity_id != parent_id:
            self.remove_child(prior.entity_id, child_id)
            parent = self._entities[parent_id]

        if not parent.has_child(child_id):
            parent = dataclasses.replace(parent, child_ids=parent.child_ids + (child_id,))
            self._entities[parent_id] = parent
        if child.parent_id != parent_id:
            child = dataclasses.replace(child, parent_id=parent_id)
            self._entities[child_id] = child
        return parent

    def remove_child(self, parent_id: str, child_id: str) -> GeographicEntity | None:
        parent = self._entities.get(parent_id)
        if parent is None:
            return None
        parent = dataclasses.replace(parent, child_ids=tuple(c for c in parent.child_ids if c != child_id))
        self._entities[parent_id] = parent
        child = self._entities.get(child_id)
        if child is not None and child.parent_id == parent_id:
            self._entities[child_id] = dataclasses.replace(child, parent_id=None)
        return parent

    # ── Society hosting (no tier restriction — the decoupling itself) ───

    def host_society(self, entity_id: str, society_id: str) -> GeographicEntity | None:
        """Host a society at any geographic entity, any tier. Strict
        single-host: a society hosted elsewhere is detached first, same
        remove-then-add pattern add_child uses for physical containment —
        but here there is no tier restriction at all."""
        entity = self._entities.get(entity_id)
        if entity is None:
            return None
        prior = self.entity_for_society(society_id)
        if prior is not None and prior.entity_id != entity_id:
            self.unhost_society(prior.entity_id, society_id)
            entity = self._entities[entity_id]
        if entity.hosts_society(society_id):
            return entity
        updated = dataclasses.replace(entity, hosted_society_ids=entity.hosted_society_ids + (society_id,))
        self._entities[entity_id] = updated
        return updated

    def unhost_society(self, entity_id: str, society_id: str) -> GeographicEntity | None:
        entity = self._entities.get(entity_id)
        if entity is None:
            return None
        updated = dataclasses.replace(
            entity,
            hosted_society_ids=tuple(s for s in entity.hosted_society_ids if s != society_id),
        )
        self._entities[entity_id] = updated
        return updated

    def set_world_location(self, entity_id: str, world_location_id: str | None) -> GeographicEntity | None:
        """Link (or clear, with None) this entity's world_location_id — the
        documented-but-previously-unwired bridge to kernel/society/
        world.py::WorldLocation's real lat/lon (see GeographicEntity's own
        docstring on the field). No validation that world_location_id
        resolves to a real WorldLocation: this registry has no reference to
        PlanetaryRuntime.world, so that check belongs to the caller (the
        API route), same separation create()'s parent_id validation uses."""
        entity = self._entities.get(entity_id)
        if entity is None:
            return None
        updated = dataclasses.replace(entity, world_location_id=world_location_id)
        self._entities[entity_id] = updated
        return updated

    def entity_for_society(self, society_id: str) -> GeographicEntity | None:
        return next((e for e in self._entities.values() if e.hosts_society(society_id)), None)

    def ancestor_of_type(self, entity_id: str, entity_type: GeographicEntityType) -> GeographicEntity | None:
        """Walk the parent chain from entity_id upward until an ancestor of
        entity_type is found, or the root is reached. Generalizes the old
        get_country_for_society's fixed two-hop (City -> Country) walk to
        any tier distance."""
        current = self._entities.get(entity_id)
        seen: set[str] = set()
        while current is not None and current.entity_id not in seen:
            if current.entity_type == entity_type:
                return current
            seen.add(current.entity_id)
            current = self.parent_of(current.entity_id)
        return None

    def societies_at_or_above(self, entity_id: str) -> set[str]:
        """Every society_id hosted at entity_id or any of its ancestors —
        "which Societies govern this location," the up-the-tree dual of
        spaces_for_society()'s down-the-tree walk. A society hosted higher
        in the hierarchy (e.g. at a City) governs every Space beneath it,
        consistent with host_society()'s "no tier restriction" model."""
        societies: set[str] = set()
        current = self._entities.get(entity_id)
        seen: set[str] = set()
        while current is not None and current.entity_id not in seen:
            seen.add(current.entity_id)
            societies.update(current.hosted_society_ids)
            current = self.parent_of(current.entity_id)
        return societies

    def spaces_for_society(self, society_id: str) -> tuple[GeographicEntity, ...]:
        """Every SPACE-tier entity associated with society_id: the entity
        it's hosted at directly if that entity IS a Space, else every SPACE
        descendant beneath it (a society hosted at, say, a City governs
        every Space in that City) — the down-the-tree dual of
        societies_at_or_above()."""
        host = self.entity_for_society(society_id)
        if host is None:
            return ()
        if host.entity_type == GeographicEntityType.SPACE:
            return (host,)
        spaces: list[GeographicEntity] = []
        seen: set[str] = set()
        stack = list(self.children_of(host.entity_id))
        while stack:
            entity = stack.pop()
            if entity.entity_id in seen:
                continue
            seen.add(entity.entity_id)
            if entity.entity_type == GeographicEntityType.SPACE:
                spaces.append(entity)
            else:
                stack.extend(self.children_of(entity.entity_id))
        return tuple(spaces)

    def validate_society_has_space(self, society_id: str) -> None:
        """Raise ValueError if society_id has no associated Space (Prompt
        3's Space-Society Association invariant: "every Society must be
        associated with at least one Space"). Deliberately NOT invoked
        automatically by host_society()/unhost_society() — worlds are
        built incrementally (a society is routinely hosted before its
        Space subtree exists, and host_society()'s own "move" path
        transiently unhosts before re-hosting elsewhere) — callers that
        need the invariant enforced call this explicitly once a society's
        topology is actually meant to be complete."""
        if not self.spaces_for_society(society_id):
            raise ValueError(f"Society {society_id} has no associated Space")
