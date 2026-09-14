"""Entity hierarchy — typed entities for the Global World Model.

Every entity in the world derives from a common Entity base.
Passive entities never execute cognition.
Only Actors own cognitive state.

Hierarchy:
    Entity
    ├── PassiveEntity (Product, Machine, Building, Road, Sensor, ...)
    └── Actor (abstract) — owns cognition
           ├── Person
           ├── Enterprise
           ├── Community
           ├── Government
           ├── Robot
           ├── DigitalAgent
           ├── Vehicle
           └── AutonomousSystem
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EntityType(Enum):
    ENTITY = "entity"
    PERSON = "person"
    VEHICLE = "vehicle"
    CAR = "car"
    TRUCK = "truck"
    ROBOT = "robot"
    DRONE = "drone"
    MACHINE = "machine"
    PRODUCT = "product"
    BUILDING = "building"
    ROAD = "road"
    SENSOR = "sensor"
    DEVICE = "device"
    ENTERPRISE = "enterprise"
    GOVERNMENT = "government"
    COMMUNITY = "community"
    NGO = "ngo"
    INSTITUTION = "institution"
    COUNTRY = "country"
    STATE_PROVINCE = "state"
    CITY = "city"
    FACTORY = "factory"
    WAREHOUSE = "warehouse"
    STORE = "store"
    PORT = "port"
    AI_AGENT = "ai_agent"
    SERVICE = "service"
    DOCUMENT = "document"
    API = "api"
    DIGITAL_TWIN = "digital_twin"
    UNKNOWN = "unknown"


class RelationType(Enum):
    LOCATED_IN = "located_in"
    LOCATED_ON = "located_on"
    CONNECTED_TO = "connected_to"
    ADJACENT_TO = "adjacent_to"
    OWNS = "owns"
    MANAGES = "manages"
    CONTROLS = "controls"
    WORKS_FOR = "works_for"
    REPORTS_TO = "reports_to"
    MEMBER_OF = "member_of"
    DRIVES = "drives"
    RIDES = "rides"
    OPERATES = "operates"
    TRAVELS_TO = "travels_to"
    PRODUCES = "produces"
    CONSUMES = "consumes"
    SUPPLIES = "supplies"
    DELIVERS = "delivers"
    REGULATES = "regulates"
    LICENSES = "licenses"
    TAXES = "taxes"
    INSPECTS = "inspects"
    USES = "uses"
    DEPENDS_ON = "depends_on"
    COMMUNICATES_WITH = "communicates_with"
    OBSERVES = "observes"
    TRANSITIONS_TO = "transitions_to"
    RELATED_TO = "related_to"
    UNKNOWN = "unknown"


# ── Base Entity ──────────────────────────────────────────────────────────────


@dataclass
class Entity:
    """Base entity — everything in the world."""

    id: str
    entity_type: EntityType = EntityType.ENTITY
    domain: str = "default"
    attributes: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    tenant_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def update_state(self, **kwargs: Any) -> None:
        self.state.update(kwargs)
        self.updated_at = time.time()

    def update_attributes(self, **kwargs: Any) -> None:
        self.attributes.update(kwargs)
        self.updated_at = time.time()


# ── Passive Entities (never execute cognition) ───────────────────────────────


@dataclass
class PassiveEntity(Entity):
    """A passive entity that never executes cognition."""

    pass


@dataclass
class Product(PassiveEntity):
    entity_type: EntityType = EntityType.PRODUCT
    category: str = ""
    unit: str = ""
    brand: str = ""


@dataclass
class Machine(PassiveEntity):
    entity_type: EntityType = EntityType.MACHINE
    capacity: float = 0.0
    status: str = "idle"


@dataclass
class Building(PassiveEntity):
    entity_type: EntityType = EntityType.BUILDING
    address: str = ""


@dataclass
class Road(PassiveEntity):
    entity_type: EntityType = EntityType.ROAD
    road_type: str = ""
    blocked: bool = False


@dataclass
class Sensor(PassiveEntity):
    entity_type: EntityType = EntityType.SENSOR
    sensor_type: str = ""
    reading: float = 0.0


@dataclass
class Device(PassiveEntity):
    entity_type: EntityType = EntityType.DEVICE
    device_type: str = ""


@dataclass
class Document(PassiveEntity):
    entity_type: EntityType = EntityType.DOCUMENT
    doc_type: str = ""
    content: str = ""


# ── Organization ─────────────────────────────────────────────────────────────


@dataclass
class Organization(PassiveEntity):
    entity_type: EntityType = EntityType.ENTERPRISE
    name: str = ""
    legal_form: str = ""


@dataclass
class Enterprise(Organization):
    entity_type: EntityType = EntityType.ENTERPRISE
    revenue: float = 0.0
    employees: int = 0


@dataclass
class Government(Organization):
    entity_type: EntityType = EntityType.GOVERNMENT
    jurisdiction: str = ""


@dataclass
class Community(Organization):
    entity_type: EntityType = EntityType.COMMUNITY
    population: int = 0


@dataclass
class NGO(Organization):
    entity_type: EntityType = EntityType.NGO


@dataclass
class Institution(Organization):
    entity_type: EntityType = EntityType.INSTITUTION


# ── Location ─────────────────────────────────────────────────────────────────


@dataclass
class Location(PassiveEntity):
    entity_type: EntityType = EntityType.CITY
    lat: float = 0.0
    lon: float = 0.0


@dataclass
class Country(Location):
    entity_type: EntityType = EntityType.COUNTRY
    code: str = ""


@dataclass
class StateProvince(Location):
    entity_type: EntityType = EntityType.STATE_PROVINCE


@dataclass
class City(Location):
    entity_type: EntityType = EntityType.CITY


@dataclass
class Factory(Location):
    entity_type: EntityType = EntityType.FACTORY


@dataclass
class Warehouse(Location):
    entity_type: EntityType = EntityType.WAREHOUSE


@dataclass
class Store(Location):
    entity_type: EntityType = EntityType.STORE


@dataclass
class Port(Location):
    entity_type: EntityType = EntityType.PORT


# ── Digital Entities ─────────────────────────────────────────────────────────


@dataclass
class DigitalEntity(PassiveEntity):
    entity_type: EntityType = EntityType.AI_AGENT
    version: str = ""


@dataclass
class Service(DigitalEntity):
    entity_type: EntityType = EntityType.SERVICE


@dataclass
class API(DigitalEntity):
    entity_type: EntityType = EntityType.API
    endpoint: str = ""


@dataclass
class DigitalTwin(DigitalEntity):
    entity_type: EntityType = EntityType.DIGITAL_TWIN
    physical_entity_id: str = ""


# ── Actors (own cognitive state) ─────────────────────────────────────────────


@dataclass
class Actor(Entity):
    """Abstract actor — owns cognition. Every autonomous entity derives from this."""

    entity_type: EntityType = EntityType.ENTITY
    is_actor: bool = True

    def observe(self, world: Any, context: Any = None) -> Any:
        """Observe the world. Returns an observation."""
        raise NotImplementedError

    def update_beliefs(self, observation: Any) -> None:
        """Update beliefs from observation."""
        raise NotImplementedError

    def plan(self, goal: str, world: Any, constraints: Any = None) -> Any:
        """Plan a path to goal."""
        raise NotImplementedError

    def execute(self, plan: Any, world: Any) -> Any:
        """Execute a plan."""
        raise NotImplementedError

    def learn(self, outcome: Any) -> None:
        """Learn from outcome."""
        raise NotImplementedError

    def compile_phi(self) -> Any:
        """Compile Bellman policy into sparse transition operator."""
        raise NotImplementedError

    def predict(self, world_clone: Any) -> Any:
        """Predict next state on cloned world."""
        raise NotImplementedError

    def commit(self) -> None:
        """Commit state after execution."""
        raise NotImplementedError


@dataclass
class Person(Actor):
    entity_type: EntityType = EntityType.PERSON
    name: str = ""
    role: str = ""


@dataclass
class Vehicle(Actor):
    entity_type: EntityType = EntityType.VEHICLE
    capacity_kg: float = 0.0
    fuel_type: str = ""


@dataclass
class Car(Vehicle):
    entity_type: EntityType = EntityType.CAR
    plate: str = ""


@dataclass
class Truck(Vehicle):
    entity_type: EntityType = EntityType.TRUCK
    axles: int = 2


@dataclass
class Robot(Actor):
    entity_type: EntityType = EntityType.ROBOT
    robot_type: str = ""


@dataclass
class Drone(Actor):
    entity_type: EntityType = EntityType.DRONE
    max_altitude_m: float = 120.0


@dataclass
class DigitalAgent(Actor):
    entity_type: EntityType = EntityType.AI_AGENT
    model: str = ""


@dataclass
class AutonomousSystem(Actor):
    entity_type: EntityType = EntityType.AI_AGENT
    system_type: str = ""


# ── Relationship ─────────────────────────────────────────────────────────────


@dataclass
class Relationship:
    """First-class typed relationship between two entities."""

    id: str
    source_id: str
    target_id: str
    relation_type: RelationType = RelationType.RELATED_TO
    direction: str = "forward"
    domain: str = "default"
    attributes: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    tenant_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


# ── Entity Registry ──────────────────────────────────────────────────────────


class EntityRegistry:
    """Registry for entity instances. Lookup-or-create."""

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}

    def register(self, entity: Entity) -> Entity:
        existing = self._entities.get(entity.id)
        if existing is not None:
            return existing
        self._entities[entity.id] = entity
        return entity

    def get(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def get_or_create(self, entity_id: str, entity_type: EntityType = EntityType.ENTITY, **kwargs: Any) -> Entity:
        entity = self._entities.get(entity_id)
        if entity is None:
            entity = Entity(id=entity_id, entity_type=entity_type, **kwargs)
            self._entities[entity_id] = entity
        return entity

    def by_type(self, entity_type: EntityType) -> list[Entity]:
        return [e for e in self._entities.values() if e.entity_type == entity_type]

    def by_domain(self, domain: str) -> list[Entity]:
        return [e for e in self._entities.values() if e.domain == domain]

    def actors(self) -> list[Actor]:
        return [e for e in self._entities.values() if isinstance(e, Actor)]

    def all(self) -> list[Entity]:
        return list(self._entities.values())

    def __len__(self) -> int:
        return len(self._entities)

    def __contains__(self, entity_id: str) -> bool:
        return entity_id in self._entities
