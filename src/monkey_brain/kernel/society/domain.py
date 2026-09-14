"""Society Domain Model (Step 12.1) — formal, immutable domain models for
a Society independent of any communication protocol or deployment topology.

Every type is a frozen dataclass: construction-only, no mutation, fully
serializable. The model describes WHAT actors, relationships, and
societies are — not HOW they communicate or persist.

Actor types cover the full spectrum from biological to digital:
    Human, AI Agent, Robot, Enterprise, Government, Community,
    Digital Service, Device.

Each actor describes:
    Identity, Type, Capabilities, Goals, Policies, Belief State,
    Trust Level, Ownership, Metadata.

The society model must be independent of networking, messaging, or
persistence — confirmed by an ownership-boundary test that parses
actual imports.

Serialization contract:
    Every type provides to_dict() -> dict and from_dict(d) -> Self.
    Enums serialize as their string value. Tuples serialize as lists.
    Nested dataclasses serialize recursively. Timestamps serialize as
    float epoch seconds. UUIDs serialize as hex strings.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


# ═══════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════


class ActorType(Enum):
    HUMAN = "human"
    AI_AGENT = "ai_agent"
    ROBOT = "robot"
    ENTERPRISE = "enterprise"
    GOVERNMENT = "government"
    COMMUNITY = "community"
    DIGITAL_SERVICE = "digital_service"
    DEVICE = "device"


class ActorStatus(Enum):
    """Step 13.6: REGISTERED/INITIALIZED added to make the actor lifecycle
    explicit — extending this enum rather than introducing a second, parallel
    lifecycle enum (the two would have overlapped on ACTIVE/SUSPENDED/RETIRED).

    Actor Lifecycle Controller (kernel/society/actor_lifecycle_controller.py):
    this is the OBSERVED state an actor's own runtime reports, distinct from
    ActorDesiredState (actor_lifecycle.py) — what the control plane wants.
    SUSPENDED and RETIRED were declared here but never assigned anywhere in
    the codebase before the Lifecycle Controller; SUSPENDED is now real
    (ActorLifecycleController.suspend_actor). RETIRED remains unused/
    deprecated — TERMINATED (added below) is the controller's real
    terminal-removal state and uses different, explicit vocabulary rather
    than silently repurposing RETIRED's ambiguous prior meaning. FAILED is
    also new: the controller's own crash-detection signal (a RUNNING-desired
    actor whose registry record has gone stale with no lease held)."""

    REGISTERED = "registered"
    INITIALIZED = "initialized"
    ACTIVE = "active"
    IDLE = "idle"
    SUSPENDED = "suspended"
    RETIRED = "retired"
    FAILED = "failed"
    TERMINATED = "terminated"
    UNKNOWN = "unknown"


class RelationshipType(Enum):
    """Deprecated: superseded by kernel/affiliations (Affiliation/AffiliationManager,
    actor-owned rather than Society-owned — see docs/compose/specs/2026-07-25-actor-
    centric-affiliation-design.md). /actors/{id}/relationships now reads and writes
    through kernel/affiliations/relationship_bridge.py + each actor's AffiliationManager.
    Kept here, alongside ActorRelationship and Society.relationships below, only so
    already-persisted Society blobs with a populated "relationships" field still
    deserialize; do not write new data through this path."""

    PEER = "peer"
    SUPERIOR = "superior"
    SUBORDINATE = "subordinate"
    COLLABORATOR = "collaborator"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    REGULATOR = "regulator"
    DEPENDENT = "dependent"
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class CapabilityLevel(Enum):
    NOVICE = "novice"
    COMPETENT = "competent"
    PROFICIENT = "proficient"
    EXPERT = "expert"
    MASTER = "master"


# ═══════════════════════════════════════════════════════════════════════════
# Serialization helpers
# ═══════════════════════════════════════════════════════════════════════════


def _enum_val(e: Enum) -> str:
    return e.value


def _ts(t: float) -> float:
    return round(t, 6)


def to_dict(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_dict(i) if hasattr(i, "to_dict") else (i.value if isinstance(i, Enum) else i) for i in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def _resolve_enum(cls: type[Enum], value: str) -> Enum:
    return cls(value)


def _resolve_list(items: list, item_from: Any) -> tuple:
    if item_from is None:
        return tuple(items)
    return tuple(item_from(i) for i in items)


# ═══════════════════════════════════════════════════════════════════════════
# Identity & Profile
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ActorIdentity:
    """Immutable identity for an actor in the society."""

    actor_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    actor_type: ActorType = ActorType.HUMAN
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "name": self.name,
            "actor_type": self.actor_type.value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActorIdentity:
        return cls(
            actor_id=d.get("actor_id", uuid4().hex),
            name=d.get("name", ""),
            actor_type=ActorType(d["actor_type"]) if "actor_type" in d else ActorType.HUMAN,
            description=d.get("description", ""),
        )


@dataclass(frozen=True)
class ActorCapability:
    """One capability an actor possesses, with proficiency level."""

    name: str = ""
    level: CapabilityLevel = CapabilityLevel.COMPETENT
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level.value,
            "description": self.description,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActorCapability:
        return cls(
            name=d.get("name", ""),
            level=CapabilityLevel(d["level"]) if "level" in d else CapabilityLevel.COMPETENT,
            description=d.get("description", ""),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(frozen=True)
class ActorProfile:
    """Full profile of an actor: identity, capabilities, goals, policies."""

    identity: ActorIdentity = field(default_factory=ActorIdentity)
    capabilities: tuple[ActorCapability, ...] = ()
    goals: tuple[str, ...] = ()
    policies: tuple[str, ...] = ()
    trust_level: float = 0.5
    """0.0 = completely untrusted, 1.0 = fully trusted."""
    ownership: str = ""
    """Who owns or controls this actor."""
    objective: str = ""
    """Optimization objective: 'cost', 'speed', 'reliability', or '' for default."""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "capabilities": [c.to_dict() for c in self.capabilities],
            "goals": list(self.goals),
            "policies": list(self.policies),
            "trust_level": self.trust_level,
            "ownership": self.ownership,
            "objective": self.objective,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActorProfile:
        return cls(
            identity=ActorIdentity.from_dict(d["identity"]) if "identity" in d else ActorIdentity(),
            capabilities=tuple(ActorCapability.from_dict(c) for c in d.get("capabilities", [])),
            goals=tuple(d.get("goals", [])),
            policies=tuple(d.get("policies", [])),
            trust_level=d.get("trust_level", 0.5),
            ownership=d.get("ownership", ""),
            objective=d.get("objective", ""),
            metadata=dict(d.get("metadata", {})),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Roles & Membership
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ActorRole:
    """A role an actor plays within a society or sub-group."""

    role_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    description: str = ""
    permissions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "name": self.name,
            "description": self.description,
            "permissions": list(self.permissions),
            "constraints": list(self.constraints),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActorRole:
        return cls(
            role_id=d.get("role_id", uuid4().hex),
            name=d.get("name", ""),
            description=d.get("description", ""),
            permissions=tuple(d.get("permissions", [])),
            constraints=tuple(d.get("constraints", [])),
        )


@dataclass(frozen=True)
class ActorMembership:
    """An actor's membership in a society or group."""

    actor_id: str = ""
    society_id: str = ""
    role: ActorRole = field(default_factory=ActorRole)
    joined_at: float = field(default_factory=time.time)
    is_active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "society_id": self.society_id,
            "role": self.role.to_dict(),
            "joined_at": _ts(self.joined_at),
            "is_active": self.is_active,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActorMembership:
        return cls(
            actor_id=d.get("actor_id", ""),
            society_id=d.get("society_id", ""),
            role=ActorRole.from_dict(d["role"]) if "role" in d else ActorRole(),
            joined_at=d.get("joined_at", 0.0),
            is_active=d.get("is_active", True),
            metadata=dict(d.get("metadata", {})),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Relationships & Address
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ActorRelationship:
    """A typed, directed relationship between two actors.

    Deprecated — see RelationshipType's docstring above. Retained for
    backward-compatible deserialization of already-persisted Society data.
    """

    source_actor_id: str = ""
    target_actor_id: str = ""
    relationship_type: RelationshipType = RelationshipType.PEER
    strength: float = 1.0
    """0.0 = no relationship, 1.0 = strongest possible."""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_actor_id": self.source_actor_id,
            "target_actor_id": self.target_actor_id,
            "relationship_type": self.relationship_type.value,
            "strength": self.strength,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActorRelationship:
        return cls(
            source_actor_id=d.get("source_actor_id", ""),
            target_actor_id=d.get("target_actor_id", ""),
            relationship_type=RelationshipType(d["relationship_type"])
            if "relationship_type" in d
            else RelationshipType.PEER,
            strength=d.get("strength", 1.0),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(frozen=True)
class ActorAddress:
    """A contact or routing address for an actor. Protocol-agnostic."""

    address_id: str = field(default_factory=lambda: uuid4().hex)
    actor_id: str = ""
    address_type: str = ""
    """e.g. 'urn', 'email', 'endpoint', 'physical' — caller-defined."""
    value: str = ""
    is_primary: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "address_id": self.address_id,
            "actor_id": self.actor_id,
            "address_type": self.address_type,
            "value": self.value,
            "is_primary": self.is_primary,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActorAddress:
        return cls(
            address_id=d.get("address_id", uuid4().hex),
            actor_id=d.get("actor_id", ""),
            address_type=d.get("address_type", ""),
            value=d.get("value", ""),
            is_primary=d.get("is_primary", False),
            metadata=dict(d.get("metadata", {})),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Society
# ═══════════════════════════════════════════════════════════════════════════

#: Suggested society_type values (Society as Organizational Context refactor)
#: — documentation/discovery only, never enforced: society_type stays a free
#: string so "Custom Society Types" needs zero code changes.
STANDARD_SOCIETY_TYPES: tuple[str, ...] = (
    "household",
    "company",
    "government",
    "school",
    "university",
    "hospital",
    "manufacturing_plant",
    "retail_store",
    "sports_club",
    "religious_organization",
    "military_unit",
    "research_lab",
    "open_source_project",
    "community_organization",
    "apartment_association",
    "building_management",
    "shopping_mall_management",
)


@dataclass(frozen=True)
class Society:
    """A formal, immutable society of actors. Independent of networking,
    messaging, or persistence.

    An actor may be a MEMBER_OF multiple societies simultaneously (see
    kernel/society/membership.py::SocietyMembershipRegistry) — Society is
    purely organizational context (purpose/governance/identity/function),
    never a physical container and never assumed to be an actor's only
    affiliation."""

    society_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    description: str = ""
    society_type: str = "generic"
    """Free-text organizational category — see STANDARD_SOCIETY_TYPES for
    suggested values (Household, Company, Government, ...); any string is
    valid, so custom types need no code changes."""
    activation_tags: tuple[str, ...] = ()
    """Keywords kernel/society/activation.py::SocietyActivationEngine
    matches against a goal's text to decide relevance for a given plan."""
    always_active: bool = False
    """If True, this society is always selected by SocietyActivationEngine
    regardless of goal-tag match (e.g. a Household society for its members)."""
    subscribed_events: tuple[str, ...] = ()
    """True Multi-Actor Coordination: real business event names (e.g.
    "OrderCreated") this Society reacts to — matched against a published
    ContextEvent's payload["domain_event"] by PlanetaryRuntime's
    propagation loop. Same shape/spirit as activation_tags above (a
    real, inspectable, per-society property set at creation time), but
    for event-driven coordination rather than goal-text relevance."""
    memberships: tuple[ActorMembership, ...] = ()
    relationships: tuple[ActorRelationship, ...] = ()
    """Deprecated — no longer written by the API (see ActorRelationship's
    docstring); actor-to-actor relationships now live in each actor's own
    AffiliationManager. Kept for reading already-persisted data."""
    addresses: tuple[ActorAddress, ...] = ()
    policies: tuple[str, ...] = ()
    shared_goals: tuple[str, ...] = ()
    """Goals the society as a whole is pursuing, distinct from any single
    actor's own goal (Step 12.3: Society Runtime owns collective state, not
    just scheduling)."""
    coordination_history: tuple[str, ...] = ()
    """A running log of coordination events (negotiations, resource
    allocations, conflict resolutions) — plain summary strings for now,
    matching the same "data now, structure later" precedent `policies`
    and `Plan.trace` already set elsewhere in this project; Step 12.7's
    Interaction framework is what will enrich this if needed."""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def actor_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(m.actor_id for m in self.memberships if m.is_active))

    def members_with_role(self, role_name: str) -> tuple[ActorMembership, ...]:
        return tuple(m for m in self.memberships if m.role.name == role_name and m.is_active)

    def relationships_for(self, actor_id: str) -> tuple[ActorRelationship, ...]:
        return tuple(r for r in self.relationships if r.source_actor_id == actor_id or r.target_actor_id == actor_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "society_id": self.society_id,
            "name": self.name,
            "description": self.description,
            "society_type": self.society_type,
            "activation_tags": list(self.activation_tags),
            "always_active": self.always_active,
            "subscribed_events": list(self.subscribed_events),
            "memberships": [m.to_dict() for m in self.memberships],
            "relationships": [r.to_dict() for r in self.relationships],
            "addresses": [a.to_dict() for a in self.addresses],
            "policies": list(self.policies),
            "shared_goals": list(self.shared_goals),
            "coordination_history": list(self.coordination_history),
            "metadata": dict(self.metadata),
            "created_at": _ts(self.created_at),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Society:
        return cls(
            society_id=d.get("society_id", uuid4().hex),
            name=d.get("name", ""),
            description=d.get("description", ""),
            society_type=d.get("society_type", "generic"),
            activation_tags=tuple(d.get("activation_tags", [])),
            always_active=d.get("always_active", False),
            subscribed_events=tuple(d.get("subscribed_events", [])),
            memberships=tuple(ActorMembership.from_dict(m) for m in d.get("memberships", [])),
            relationships=tuple(ActorRelationship.from_dict(r) for r in d.get("relationships", [])),
            addresses=tuple(ActorAddress.from_dict(a) for a in d.get("addresses", [])),
            policies=tuple(d.get("policies", [])),
            shared_goals=tuple(d.get("shared_goals", [])),
            coordination_history=tuple(d.get("coordination_history", [])),
            metadata=dict(d.get("metadata", {})),
            created_at=d.get("created_at", 0.0),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Team — Runtime Encapsulation Refactor follow-up: the tier beneath Society,
# containing a subset of its Actors. SocietyRuntime-owned (kernel/society/
# runtime.py), not a new runtime tier — no tick()/cycle() of its own.
# Strict membership: an actor belongs to at most one team per society,
# consistent with Country->City's exclusivity one level up the hierarchy.
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Team:
    """An immutable team of actors within one society. Holds no mutation
    logic itself; every change flows through SocietyRuntime's team methods,
    which replace it via `dataclasses.replace()` — the same pattern as
    `Society` above."""

    team_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    description: str = ""
    member_actor_ids: tuple[str, ...] = ()
    shared_goals: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def has_member(self, actor_id: str) -> bool:
        return actor_id in self.member_actor_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "name": self.name,
            "description": self.description,
            "member_actor_ids": list(self.member_actor_ids),
            "shared_goals": list(self.shared_goals),
            "metadata": dict(self.metadata),
            "created_at": _ts(self.created_at),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Team:
        return cls(
            team_id=d.get("team_id", uuid4().hex),
            name=d.get("name", ""),
            description=d.get("description", ""),
            member_actor_ids=tuple(d.get("member_actor_ids", [])),
            shared_goals=tuple(d.get("shared_goals", [])),
            metadata=dict(d.get("metadata", {})),
            created_at=d.get("created_at", 0.0),
        )
