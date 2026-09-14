"""GeographicEntity — the physical containment hierarchy, fully independent
of the organizational Society -> Team -> Actor hierarchy (kernel/society/).

    Universe -> Planet -> Region -> Country -> State -> County -> City ->
        Street -> Building -> Floor -> Space -> Coordinate

Region (between Planet/Country) and Floor (between Building/Space) are
OPTIONAL intermediate tiers — see PARENT_TIER below: Country still accepts
a Planet directly and Space still accepts a Building directly, so this
extends the original Planet->Country->...->Building->Space chain rather
than requiring every entity to route through the new tiers.
                                                                    |
                                                        +-----------+-----------+
                                                        |                       |
                                                     Actors           Associated Societies

Not (as the physical containment tree might otherwise suggest):

    Space -> Society -> Actor

Actors are not children of societies. Actors and societies are siblings
attached to a Space: a Society exists to GOVERN the space(s) it is hosted
at (GeographicRegistry.host_society); an Actor exists WITHIN a space (its
physical location, tracked by kernel/timeline/presence.py::PresenceTimeline,
not by this file's containment tree). Neither relationship nests one inside
the other.

Supersedes this session's earlier kernel/society/country.py + city.py, which
incorrectly nested Society *inside* Country/City. A geographic entity now
merely HOSTS zero or more societies (GeographicRegistry.host_society) — it
never contains them. Physical containment (this file + registry.py) and
social organization (kernel/society/domain.py::Society/Team) are two
independent graphs; the only link between them is the hosting relationship,
enforced by GeographicRegistry, not by either dataclass holding a reference
to the other's type.

Every GeographicEntity carries the same generic shape (parent, children,
hosted societies, world location, metadata, identifiers) regardless of tier
— mirroring the immutable-dataclass-plus-manager pattern already used
throughout kernel/society/ (Society, Federation, Team): this file holds pure,
frozen data; kernel/geography/registry.py::GeographicRegistry is the sole
mutating counterpart.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class GeographicEntityType(Enum):
    UNIVERSE = "universe"
    PLANET = "planet"
    REGION = "region"
    COUNTRY = "country"
    STATE = "state"
    COUNTY = "county"
    CITY = "city"
    STREET = "street"
    BUILDING = "building"
    FLOOR = "floor"
    SPACE = "space"
    COORDINATE = "coordinate"


class BuildingType(Enum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    INDUSTRIAL = "industrial"
    INSTITUTIONAL = "institutional"
    GOVERNMENT = "government"
    MIXED_USE = "mixed_use"


class SpaceType(Enum):
    APARTMENT = "apartment"
    HOUSE = "house"
    OFFICE = "office"
    RETAIL_UNIT = "retail_unit"
    RESTAURANT = "restaurant"
    HOTEL_ROOM = "hotel_room"
    FACTORY_FLOOR = "factory_floor"
    WAREHOUSE_ZONE = "warehouse_zone"
    CLASSROOM = "classroom"
    HOSPITAL_WARD = "hospital_ward"
    STORAGE_UNIT = "storage_unit"
    PARKING_SPACE = "parking_space"


@dataclass(frozen=True)
class GeographicEntity:
    """The common base type every physical-hierarchy tier subclasses.

    Immutable — every change (add_child, host_society, ...) flows through
    GeographicRegistry, which replaces it via dataclasses.replace(), the
    same pattern Society/Federation/Team already use in this codebase.
    """

    entity_id: str = field(default_factory=lambda: uuid4().hex)
    entity_type: GeographicEntityType = GeographicEntityType.CITY
    name: str = ""
    description: str = ""
    parent_id: str | None = None
    child_ids: tuple[str, ...] = ()
    hosted_society_ids: tuple[str, ...] = ()
    """Societies hosted HERE — the decoupling mechanism itself. No tier
    restriction: a Society may be hosted by a Country, a Building, a Space,
    anything. Distinct from child_ids, which is strict physical containment
    of other GeographicEntity instances one tier down."""
    world_location_id: str | None = None
    """Optional link to an existing kernel/society/world.py::WorldLocation
    id — reuses that store's name/address/lat/lon rather than duplicating
    coordinate fields here."""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def has_child(self, child_id: str) -> bool:
        return child_id in self.child_ids

    def hosts_society(self, society_id: str) -> bool:
        return society_id in self.hosted_society_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type.value,
            "name": self.name,
            "description": self.description,
            "parent_id": self.parent_id,
            "child_ids": list(self.child_ids),
            "hosted_society_ids": list(self.hosted_society_ids),
            "world_location_id": self.world_location_id,
            "metadata": dict(self.metadata),
            "created_at": round(self.created_at, 6),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GeographicEntity":
        return cls(
            entity_id=d.get("entity_id", uuid4().hex),
            name=d.get("name", ""),
            description=d.get("description", ""),
            parent_id=d.get("parent_id"),
            child_ids=tuple(d.get("child_ids", [])),
            hosted_society_ids=tuple(d.get("hosted_society_ids", [])),
            world_location_id=d.get("world_location_id"),
            metadata=dict(d.get("metadata", {})),
            created_at=d.get("created_at", 0.0),
        )


# ── Concrete tiers — each pins entity_type, mirroring kernel/compile/
# entity.py's existing (dead, unrelated) Location/Country/City pattern. ──


@dataclass(frozen=True)
class Universe(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.UNIVERSE


@dataclass(frozen=True)
class Planet(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.PLANET


@dataclass(frozen=True)
class Region(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.REGION


@dataclass(frozen=True)
class Country(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.COUNTRY


@dataclass(frozen=True)
class State(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.STATE


@dataclass(frozen=True)
class County(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.COUNTY


@dataclass(frozen=True)
class City(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.CITY


@dataclass(frozen=True)
class Street(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.STREET


@dataclass(frozen=True)
class Building(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.BUILDING
    building_type: BuildingType = BuildingType.MIXED_USE


@dataclass(frozen=True)
class Floor(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.FLOOR


@dataclass(frozen=True)
class Space(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.SPACE
    space_type: SpaceType = SpaceType.OFFICE


@dataclass(frozen=True)
class Coordinate(GeographicEntity):
    entity_type: GeographicEntityType = GeographicEntityType.COORDINATE


# ── Typed Building subtypes — structural only (no added fields/behavior
# this pass); the runtime depends only on the abstract Building/Space. ──


@dataclass(frozen=True)
class ResidentialBuilding(Building):
    building_type: BuildingType = BuildingType.RESIDENTIAL


@dataclass(frozen=True)
class CommercialBuilding(Building):
    building_type: BuildingType = BuildingType.COMMERCIAL


@dataclass(frozen=True)
class IndustrialBuilding(Building):
    building_type: BuildingType = BuildingType.INDUSTRIAL


@dataclass(frozen=True)
class InstitutionalBuilding(Building):
    building_type: BuildingType = BuildingType.INSTITUTIONAL


@dataclass(frozen=True)
class GovernmentBuilding(Building):
    building_type: BuildingType = BuildingType.GOVERNMENT


@dataclass(frozen=True)
class MixedUseBuilding(Building):
    building_type: BuildingType = BuildingType.MIXED_USE


# ── Typed Space subtypes — structural only. ──────────────────────────────


@dataclass(frozen=True)
class Apartment(Space):
    space_type: SpaceType = SpaceType.APARTMENT


@dataclass(frozen=True)
class House(Space):
    space_type: SpaceType = SpaceType.HOUSE


@dataclass(frozen=True)
class Office(Space):
    space_type: SpaceType = SpaceType.OFFICE


@dataclass(frozen=True)
class RetailUnit(Space):
    space_type: SpaceType = SpaceType.RETAIL_UNIT


@dataclass(frozen=True)
class Restaurant(Space):
    space_type: SpaceType = SpaceType.RESTAURANT


@dataclass(frozen=True)
class HotelRoom(Space):
    space_type: SpaceType = SpaceType.HOTEL_ROOM


@dataclass(frozen=True)
class FactoryFloor(Space):
    space_type: SpaceType = SpaceType.FACTORY_FLOOR


@dataclass(frozen=True)
class WarehouseZone(Space):
    space_type: SpaceType = SpaceType.WAREHOUSE_ZONE


@dataclass(frozen=True)
class Classroom(Space):
    space_type: SpaceType = SpaceType.CLASSROOM


@dataclass(frozen=True)
class HospitalWard(Space):
    space_type: SpaceType = SpaceType.HOSPITAL_WARD


@dataclass(frozen=True)
class StorageUnit(Space):
    space_type: SpaceType = SpaceType.STORAGE_UNIT


@dataclass(frozen=True)
class ParkingSpace(Space):
    space_type: SpaceType = SpaceType.PARKING_SPACE


# entity_type -> the set of tiers it may be parented under (see
# GeographicRegistry.add_child). A tier with no entry has no required
# parent (see ROOT_ELIGIBLE below for which tiers may therefore be
# created with parent_id=None).
#
# REGION and FLOOR are optional insertions (Universe/Region/Floor/
# Coordinate extend the original 8-tier hierarchy without breaking it):
# COUNTRY accepts a Planet directly OR a Region, and SPACE accepts a
# Building directly OR a Floor, so existing Planet->Country and
# Building->Space data/callers keep working unchanged whether or not the
# optional intermediate tier is ever used.
PARENT_TIER: dict[GeographicEntityType, frozenset[GeographicEntityType]] = {
    GeographicEntityType.PLANET: frozenset({GeographicEntityType.UNIVERSE}),
    GeographicEntityType.REGION: frozenset({GeographicEntityType.PLANET}),
    GeographicEntityType.COUNTRY: frozenset(
        {
            GeographicEntityType.PLANET,
            GeographicEntityType.REGION,
        }
    ),
    GeographicEntityType.STATE: frozenset({GeographicEntityType.COUNTRY}),
    GeographicEntityType.COUNTY: frozenset({GeographicEntityType.STATE}),
    GeographicEntityType.CITY: frozenset({GeographicEntityType.COUNTY}),
    GeographicEntityType.STREET: frozenset({GeographicEntityType.CITY}),
    GeographicEntityType.BUILDING: frozenset({GeographicEntityType.STREET}),
    GeographicEntityType.FLOOR: frozenset({GeographicEntityType.BUILDING}),
    GeographicEntityType.SPACE: frozenset(
        {
            GeographicEntityType.BUILDING,
            GeographicEntityType.FLOOR,
        }
    ),
    GeographicEntityType.COORDINATE: frozenset({GeographicEntityType.SPACE}),
}

# Tiers that may be created with parent_id=None (true roots of the
# hierarchy). PLANET stays root-eligible for backward compatibility even
# though it now also has a PARENT_TIER entry (UNIVERSE) — a Planet may
# optionally be attached under a Universe via add_child(), but is not
# required to be.
ROOT_ELIGIBLE: frozenset[GeographicEntityType] = frozenset(
    {
        GeographicEntityType.UNIVERSE,
        GeographicEntityType.PLANET,
    }
)
