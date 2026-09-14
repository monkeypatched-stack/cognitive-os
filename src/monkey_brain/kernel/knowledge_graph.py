"""Domain Metadata Knowledge Graph — Comprehensive entity-relationship system.

A knowledge graph centered on a Person/Actor with typed relationships:
- Identity, Family, Employment, Business, Financial, Assets, Investments
- Location, Behavior, Health, Education, Insurance, Retirement
- Trust & Estate, Foreign, Temporal tracking

Usage:
    from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph(person_id="alice")
    kg.add_entity("employer", "Acme Corp", {"salary": 100000})
    kg.add_relationship("alice", "WORKS_FOR", "employer")
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("agentos.knowledge_graph")

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def _index_keywords(name: str) -> set[str]:
    """Generic (domain-agnostic) tokenization for the keyword index —
    lowercase alphanumeric words. Deliberately simpler than any single
    domain's own matching logic (e.g. grocery.py's _keywords/_match_score,
    which layers substitution policies and stopwords on top) — this is
    only ever a fast PRE-FILTER to shrink a huge entity set down to real
    candidates; callers still run their own precise scoring over
    whatever this returns."""
    return {w.lower() for w in _WORD_RE.findall(name or "")}


# ═══════════════════════════════════════════════════════════════════════════════
# Entity Types
# ═══════════════════════════════════════════════════════════════════════════════


class EntityType(str, Enum):
    """Types of entities in the knowledge graph."""

    PERSON = "person"
    ORGANIZATION = "organization"
    ADDRESS = "address"
    ASSET = "asset"
    ACCOUNT = "account"
    INVESTMENT = "investment"
    INCOME = "income"
    EXPENSE = "expense"
    LIABILITY = "liability"
    INSURANCE = "insurance"
    RETIREMENT = "retirement"
    TRUST = "trust"
    EDUCATION = "education"
    HEALTH = "health"
    BUSINESS = "business"
    VEHICLE = "vehicle"
    PROPERTY = "property"
    LOAN = "loan"
    DEBT = "debt"
    GIFT = "gift"
    DONATION = "donation"
    TAX = "tax"
    DOCUMENT = "document"
    EVENT = "event"
    OTHER = "other"


class RelationshipType(str, Enum):
    """Types of relationships in the knowledge graph."""

    # Identity
    LIVES_AT = "LIVES_AT"
    BORN_ON = "BORN_ON"
    IDENTIFIED_BY = "IDENTIFIED_BY"

    # Family
    MARRIED_TO = "MARRIED_TO"
    PARENT_OF = "PARENT_OF"
    CHILD_OF = "CHILD_OF"
    SIBLING_OF = "SIBLING_OF"
    SPOUSE_OF = "SPOUSE_OF"
    DEPENDENT_OF = "DEPENDENT_OF"
    GUARDIAN_OF = "GUARDIAN_OF"

    # Employment
    WORKS_FOR = "WORKS_FOR"
    EMPLOYS = "EMPLOYS"
    CONTRACTS_WITH = "CONTRACTS_WITH"
    REPORTS_TO = "REPORTS_TO"

    # Business
    OWNS_BUSINESS = "OWNS_BUSINESS"
    OPERATES = "OPERATES"
    INVESTS_IN = "INVESTS_IN"

    # Financial
    RECEIVES_INCOME = "RECEIVES_INCOME"
    PAYS_EXPENSE = "PAYS_EXPENSE"
    HOLDS_ACCOUNT = "HOLDS_ACCOUNT"
    OWNS = "OWNS"
    RENTS = "RENTS"

    # Assets
    POSSESSES = "POSSESSES"
    DEPRECIATES = "DEPRECIATES"
    MAINTAINS = "MAINTAINS"

    # Investments
    HOLDINGS = "HOLDINGS"
    CONTRIBUTES_TO = "CONTRIBUTES_TO"
    WITHDRAWS_FROM = "WITHDRAWS_FROM"

    # Insurance
    INSURED_BY = "INSURED_BY"
    COVERS = "COVERS"
    BENEFICIARY_OF = "BENEFICIARY_OF"

    # Education
    ATTENDS = "ATTENDS"
    GRADUATED_FROM = "GRADUATED_FROM"
    BORROWS_FROM = "BORROWS_FROM"

    # Health
    TREATED_BY = "TREATED_BY"
    PROVIDER = "PROVIDER"

    # Trust & Estate
    TRUSTEE_OF = "TRUSTEE_OF"
    BENEFICIARY_OF_TRUST = "BENEFICIARY_OF_TRUST"
    INHERITS = "INHERITS"

    # Foreign
    FOREIGN_ACCOUNT = "FOREIGN_ACCOUNT"
    FOREIGN_INCOME = "FOREIGN_INCOME"

    # Temporal
    PRECEDED_BY = "PRECEDED_BY"
    SUCCEEDED_BY = "SUCCEEDED_BY"
    CONCURRENT_WITH = "CONCURRENT_WITH"

    # Generic
    RELATED_TO = "RELATED_TO"
    DEPENDS_ON = "DEPENDS_ON"
    PART_OF = "PART_OF"
    HAS_MEMBER = "HAS_MEMBER"


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Entity:
    """An entity in the knowledge graph."""

    entity_id: str
    entity_type: EntityType = EntityType.OTHER
    name: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def update(self, **kwargs: Any) -> None:
        """Update entity attributes."""
        for key, value in kwargs.items():
            if key in ("attributes", "metadata"):
                getattr(self, key).update(value)
            else:
                setattr(self, key, value)
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type.value,
            "name": self.name,
            "attributes": dict(self.attributes),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entity:
        return cls(
            entity_id=data.get("entity_id", ""),
            entity_type=EntityType(data.get("entity_type", "other")),
            name=data.get("name", ""),
            attributes=data.get("attributes", {}),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


@dataclass
class Relationship:
    """A relationship between two entities."""

    relationship_id: str = ""
    source_id: str = ""
    target_id: str = ""
    relationship_type: RelationshipType = RelationshipType.RELATED_TO
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    start_date: str = ""
    end_date: str = ""
    is_active: bool = True
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relationship_type": self.relationship_type.value,
            "attributes": dict(self.attributes),
            "confidence": self.confidence,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "is_active": self.is_active,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Relationship:
        return cls(
            relationship_id=data.get("relationship_id", ""),
            source_id=data.get("source_id", ""),
            target_id=data.get("target_id", ""),
            relationship_type=RelationshipType(data.get("relationship_type", "RELATED_TO")),
            attributes=data.get("attributes", {}),
            confidence=data.get("confidence", 1.0),
            start_date=data.get("start_date", ""),
            end_date=data.get("end_date", ""),
            is_active=data.get("is_active", True),
            created_at=data.get("created_at", time.time()),
        )


@dataclass
class TemporalSnapshot:
    """A point-in-time snapshot of entity state."""

    snapshot_id: str = ""
    entity_id: str = ""
    year: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)
    relationships: list[str] = field(default_factory=list)  # relationship IDs
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "entity_id": self.entity_id,
            "year": self.year,
            "attributes": dict(self.attributes),
            "relationships": list(self.relationships),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemporalSnapshot:
        return cls(
            snapshot_id=data.get("snapshot_id", ""),
            entity_id=data.get("entity_id", ""),
            year=data.get("year", 0),
            attributes=data.get("attributes", {}),
            relationships=data.get("relationships", []),
            created_at=data.get("created_at", time.time()),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Domain-Specific Entity Classes
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class PersonEntity(Entity):
    """Person entity with identity metadata."""

    ssn: str = ""
    dob: str = ""
    gender: str = ""
    marital_status: str = ""
    citizenship: str = ""
    residency_status: str = ""

    def __post_init__(self):
        self.entity_type = EntityType.PERSON


@dataclass
class AddressEntity(Entity):
    """Address entity."""

    street: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    country: str = "US"
    is_primary: bool = True
    is_mailing: bool = False

    def __post_init__(self):
        self.entity_type = EntityType.ADDRESS


@dataclass
class OrganizationEntity(Entity):
    """Organization entity."""

    ein: str = ""
    industry: str = ""
    size: str = ""
    website: str = ""
    is_employer: bool = False
    is_client: bool = False
    is_vendor: bool = False

    def __post_init__(self):
        self.entity_type = EntityType.ORGANIZATION


@dataclass
class AssetEntity(Entity):
    """Asset entity."""

    asset_type: str = ""
    purchase_date: str = ""
    purchase_price: float = 0.0
    current_value: float = 0.0
    depreciation_method: str = ""
    depreciation_rate: float = 0.0
    is_depreciable: bool = False

    def __post_init__(self):
        self.entity_type = EntityType.ASSET


@dataclass
class InvestmentEntity(Entity):
    """Investment entity."""

    investment_type: str = ""  # stock, bond, crypto, mutual_fund, etf
    ticker: str = ""
    shares: float = 0.0
    cost_basis: float = 0.0
    current_value: float = 0.0
    purchase_date: str = ""
    account_type: str = ""  # brokerage, ira, 401k, roth

    def __post_init__(self):
        self.entity_type = EntityType.INVESTMENT


@dataclass
class IncomeEntity(Entity):
    """Income entity."""

    income_type: str = ""  # salary, wages, interest, dividend, capital_gain, rental, self_employment
    amount: float = 0.0
    frequency: str = ""  # annual, monthly, weekly, one_time
    source: str = ""
    year: int = 0
    is_taxable: bool = True

    def __post_init__(self):
        self.entity_type = EntityType.INCOME


@dataclass
class ExpenseEntity(Entity):
    """Expense entity."""

    expense_type: str = ""  # mortgage, tuition, medical, charitable, business, personal
    amount: float = 0.0
    frequency: str = ""
    is_deductible: bool = False
    year: int = 0

    def __post_init__(self):
        self.entity_type = EntityType.EXPENSE


@dataclass
class LiabilityEntity(Entity):
    """Liability entity."""

    liability_type: str = ""  # mortgage, student_loan, car_loan, credit_card, personal_loan
    original_amount: float = 0.0
    current_balance: float = 0.0
    interest_rate: float = 0.0
    monthly_payment: float = 0.0
    start_date: str = ""
    end_date: str = ""

    def __post_init__(self):
        self.entity_type = EntityType.LIABILITY


@dataclass
class InsuranceEntity(Entity):
    """Insurance entity."""

    insurance_type: str = ""  # health, life, auto, home, umbrella, disability
    provider: str = ""
    policy_number: str = ""
    premium: float = 0.0
    coverage_amount: float = 0.0
    deductible: float = 0.0
    start_date: str = ""
    end_date: str = ""

    def __post_init__(self):
        self.entity_type = EntityType.INSURANCE


@dataclass
class RetirementEntity(Entity):
    """Retirement account entity."""

    account_type: str = ""  # ira, roth_ira, 401k, 403b, pension, sep_ira
    provider: str = ""
    balance: float = 0.0
    annual_contribution: float = 0.0
    employer_match: float = 0.0
    vesting_date: str = ""

    def __post_init__(self):
        self.entity_type = EntityType.RETIREMENT


@dataclass
class TrustEntity(Entity):
    """Trust entity."""

    trust_type: str = ""  # revocable, irrevocable, living, testamentary
    trustee: str = ""
    beneficiaries: list[str] = field(default_factory=list)
    assets: float = 0.0
    creation_date: str = ""
    termination_date: str = ""

    def __post_init__(self):
        self.entity_type = EntityType.TRUST


@dataclass
class EducationEntity(Entity):
    """Education entity."""

    institution: str = ""
    degree: str = ""
    field_of_study: str = ""
    start_date: str = ""
    end_date: str = ""
    tuition: float = 0.0
    scholarships: float = 0.0
    student_loans: float = 0.0
    is_enrolled: bool = False

    def __post_init__(self):
        self.entity_type = EntityType.EDUCATION


@dataclass
class HealthEntity(Entity):
    """Health-related entity."""

    health_type: str = ""  # hsa, medical_expense, provider, condition
    provider: str = ""
    amount: float = 0.0
    is_deductible: bool = False
    year: int = 0

    def __post_init__(self):
        self.entity_type = EntityType.HEALTH


@dataclass
class BusinessEntity(Entity):
    """Business entity."""

    business_type: str = ""  # llc, s_corp, c_corp, sole_proprietorship, partnership
    ein: str = ""
    industry: str = ""
    annual_revenue: float = 0.0
    annual_expenses: float = 0.0
    employees: int = 0
    start_date: str = ""
    is_active: bool = True

    def __post_init__(self):
        self.entity_type = EntityType.BUSINESS


# ═══════════════════════════════════════════════════════════════════════════════
# Knowledge Graph
# ═══════════════════════════════════════════════════════════════════════════════


class KnowledgeGraph:
    """Comprehensive knowledge graph for domain metadata.

    Features:
    - Typed entities and relationships
    - Temporal snapshots
    - Graph traversal
    - Domain-specific queries
    - Serialization
    - Structured logging via existing logger
    - Integration with ContextEventStore for persistence
    """

    def __init__(self, person_id: str = "", context_event_store: Any = None):
        self.person_id = person_id
        self._entities: dict[str, Entity] = {}
        self._relationships: dict[str, Relationship] = {}
        self._adjacency: dict[str, list[str]] = {}  # entity_id -> [relationship_ids]
        self._reverse_adjacency: dict[str, list[str]] = {}
        self._snapshots: list[TemporalSnapshot] = []
        self._context_event_store = context_event_store  # Existing persistence
        self._entity_version: dict[str, int] = {}
        # Phase 6 performance certification (GS-6000): real indexes so
        # entity_type/keyword lookups are O(k) (k = matches only) instead
        # of a full O(n) scan over EVERY entity in the graph, regardless
        # of how large it grows — every domain call site that did
        # `for e in kg.entities if e.entity_type == X` (open_products,
        # predict_demand, check_recent_duplicate_purchase,
        # find_household_pantry_stock, ...) paid that full scan on every
        # single call. Measured live: at 1M catalog entities this was the
        # difference between a sub-millisecond indexed lookup and a
        # multi-second linear scan per request.
        self._entities_by_type: dict[EntityType, dict[str, Entity]] = {}
        self._entity_keywords: dict[str, set[str]] = {}  # entity_id -> its own keyword set, for clean removal/update
        self._keyword_index: dict[str, set[str]] = {}  # keyword -> entity_ids
        # Gate 6 (Persistence): this graph itself has no persistence layer
        # (the context_event_store param above is accepted but was never
        # actually used anywhere in this file) — every Product/Order/
        # Merchant/Shipment built through the Commerce/Orders/Fulfillment
        # REST layer was pure in-memory. set_on_change() is the single
        # choke point every mutation method below already funnels
        # through, mirroring ContextStream.set_on_publish() — lets
        # PlanetaryRuntime persist incrementally (one entity/relationship
        # per call) without this class needing to know Redis exists.
        self._on_change: Any = None

    def set_on_change(self, callback: Any) -> None:
        """callback(kind: 'entity' | 'relationship', id: str, action: 'upsert' | 'delete')"""
        self._on_change = callback

    def _notify_change(self, kind: str, obj_id: str, action: str) -> None:
        if self._on_change is not None:
            try:
                self._on_change(kind, obj_id, action)
            except Exception:
                logger.debug(
                    "KnowledgeGraph on_change callback failed for %s %s %s",
                    kind,
                    obj_id,
                    action,
                )

    # ── Indexing (internal) ──────────────────────────────────────

    def _index_add(self, entity: "Entity") -> None:
        self._entities_by_type.setdefault(entity.entity_type, {})[entity.entity_id] = entity
        kws = _index_keywords(entity.name)
        self._entity_keywords[entity.entity_id] = kws
        for kw in kws:
            self._keyword_index.setdefault(kw, set()).add(entity.entity_id)

    def _index_remove(self, entity_id: str, entity_type: "EntityType | None") -> None:
        if entity_type is not None:
            bucket = self._entities_by_type.get(entity_type)
            if bucket is not None:
                bucket.pop(entity_id, None)
        old_kws = self._entity_keywords.pop(entity_id, None) or set()
        for kw in old_kws:
            bucket = self._keyword_index.get(kw)
            if bucket is not None:
                bucket.discard(entity_id)
                if not bucket:
                    del self._keyword_index[kw]

    # ── Entity Operations ───────────────────────────────────────

    def add_entity(
        self,
        entity_id: str,
        entity_type: EntityType = EntityType.OTHER,
        name: str = "",
        attributes: dict | None = None,
        **kwargs,
    ) -> Entity:
        from src.monkey_brain.kernel.security_boundary import (
            assert_state_mutation_allowed,
        )

        assert_state_mutation_allowed("knowledge_graph.add_entity")
        """Add an entity to the graph. Calling this again with an
        entity_id that already exists overwrites it (some callers rely on
        this, e.g. a deterministic marker entity_id meant to hold only
        the latest value) — the old entry's type/keyword index entries
        are removed first, or they'd leak (a stale reference to the
        replaced entity surviving in _entities_by_type/_keyword_index
        even though self._entities already has the new one)."""
        existing = self._entities.get(entity_id)
        if existing is not None:
            self._index_remove(entity_id, existing.entity_type)
        entity = Entity(
            entity_id=entity_id,
            entity_type=entity_type,
            name=name,
            attributes=attributes or kwargs,
        )
        self._entities[entity_id] = entity
        self._index_add(entity)

        # Structured logging
        logger.info("ENTITY_ADDED: %s (%s) - %s", entity_id, entity_type.value, name)
        self._notify_change("entity", entity_id, "upsert")

        return entity

    def get_entity(self, entity_id: str) -> Entity | None:
        """Get an entity by ID."""
        return self._entities.get(entity_id)

    def refresh(self) -> None:
        """No-op here; overridden by Neo4jBackedKnowledgeGraph to discard
        its in-memory cache and re-hydrate from Neo4j.

        This plain KnowledgeGraph IS its own source of truth — there is no
        separate backing store it could be stale relative to — so callers
        that want "the current state" regardless of which backend they
        were handed (e.g. grocery.py's OrderConfirmation/BeliefCheck/
        HouseholdCognition/ProductSelection staleness checks) can call
        kg.refresh() polymorphically without special-casing the in-memory
        backend.
        """
        return None

    def update_entity(self, entity_id: str, **kwargs) -> Entity | None:
        """Update an entity."""
        from src.monkey_brain.kernel.security_boundary import (
            assert_state_mutation_allowed,
        )

        assert_state_mutation_allowed("knowledge_graph.update_entity")
        entity = self._entities.get(entity_id)
        if entity:
            old_type, old_name = entity.entity_type, entity.name
            entity.update(**kwargs)
            self._entity_version[entity_id] = self._entity_version.get(entity_id, 0) + 1
            # Re-index only if something the indexes key on actually
            # changed (type/name) — the overwhelmingly common case
            # (attribute-only updates, e.g. balance/quantity) skips this
            # entirely rather than paying an index rebuild per call.
            if entity.entity_type != old_type or entity.name != old_name:
                self._index_remove(entity_id, old_type)
                self._index_add(entity)
            logger.info("ENTITY_UPDATED: %s - changed: %s", entity_id, list(kwargs.keys()))
            self._notify_change("entity", entity_id, "upsert")
        return entity

    def version_of(self, entity_id: str) -> int:
        """Last known version for entity_id — see
        Neo4jBackedKnowledgeGraph.version_of. 0 if never updated."""
        return self._entity_version.get(entity_id, 0)

    def compare_and_swap(
        self, entity_id: str, expected_version: int, attribute_updates: dict
    ) -> tuple[bool, Entity | None]:
        """In-process analog of Neo4jBackedKnowledgeGraph.compare_and_swap.

        This graph has no separate persistence layer to race against, but
        callers (e.g. grocery.py's try_reserve, used by concurrent actors
        claiming the same entity within one process) still need the same
        atomic "only apply if nobody else changed it since I read it"
        contract. Safe here because every KnowledgeGraph method is
        synchronous with no `await` inside — an asyncio task cannot be
        preempted mid-call — so this dict mutation cannot interleave with
        another task's.
        """
        from src.monkey_brain.kernel.security_boundary import (
            assert_state_mutation_allowed,
        )

        assert_state_mutation_allowed("knowledge_graph.compare_and_swap")
        entity = self._entities.get(entity_id)
        if entity is None:
            return False, None
        if self._entity_version.get(entity_id, 0) != expected_version:
            return False, entity
        # Merge into entity.attributes, matching Neo4jBackedKnowledgeGraph's
        # `merged = {**entity.attributes, **attribute_updates}` semantics —
        # NOT entity.update(**attribute_updates), which treats an arbitrary
        # key like "reservations" or "quantity" as a top-level Entity field
        # (via setattr) rather than an attributes-dict key, silently
        # writing somewhere callers never read from.
        entity.attributes.update(attribute_updates)
        # Real gap this closed: this bypasses Entity.update() (the only
        # other real mutation path, used by update_entity() above), which
        # is the only place that bumps updated_at -- so every CAS-based
        # mutation (try_reserve/compete_for_resource's reservations, any
        # other caller of this method) left updated_at frozen at the
        # entity's original creation time forever, even though a real
        # change just happened. Confirmed live: a product just reserved
        # via CompeteForResource still showed its original listing
        # timestamp as "last updated" in the debugger, with no way to
        # tell the reservation had actually landed.
        entity.updated_at = time.time()
        self._entity_version[entity_id] = expected_version + 1
        self._notify_change("entity", entity_id, "upsert")
        return True, entity

    def remove_entity(self, entity_id: str) -> bool:
        """Remove an entity and its relationships."""
        from src.monkey_brain.kernel.security_boundary import (
            assert_state_mutation_allowed,
        )

        assert_state_mutation_allowed("knowledge_graph.remove_entity")
        if entity_id not in self._entities:
            return False

        # Remove relationships
        rel_ids = self._adjacency.get(entity_id, []) + self._reverse_adjacency.get(entity_id, [])
        for rel_id in rel_ids:
            self._relationships.pop(rel_id, None)

        removed_type = self._entities[entity_id].entity_type
        del self._entities[entity_id]
        self._index_remove(entity_id, removed_type)
        self._adjacency.pop(entity_id, None)
        self._reverse_adjacency.pop(entity_id, None)

        logger.info("ENTITY_REMOVED: %s", entity_id)
        for rel_id in rel_ids:
            self._notify_change("relationship", rel_id, "delete")
        self._notify_change("entity", entity_id, "delete")

        return True

    def entities_by_type(self, entity_type: EntityType) -> list[Entity]:
        """Get all entities of a specific type — via the real index
        (Phase 6, GS-6000), O(k) where k is only entities of this type,
        not a scan over the whole graph."""
        return list(self._entities_by_type.get(entity_type, {}).values())

    def entities_by_keyword(self, keyword: str) -> list[Entity]:
        """Phase 6 (GS-6000): every entity whose name contains this
        keyword (case-insensitive, tokenized) — via the real keyword
        index, O(1) lookup + O(k matches), instead of scanning every
        entity in the graph checking each name. A fast PRE-FILTER for
        domain code's own precise matching (e.g. grocery.py's
        _match_score), not a replacement for it — this only guarantees
        "contains this exact token," real ranking still happens on the
        (much smaller) result."""
        ids = self._keyword_index.get(keyword.lower(), set())
        return [self._entities[eid] for eid in ids if eid in self._entities]

    def entities_by_keywords(self, keywords) -> list[Entity]:
        """Union of entities matching ANY of the given keywords — same
        index-backed pre-filter as entities_by_keyword, for multi-word
        search phrases."""
        ids: set[str] = set()
        for kw in keywords:
            ids |= self._keyword_index.get(kw.lower(), set())
        return [self._entities[eid] for eid in ids if eid in self._entities]

    def entities_by_name(self, name: str) -> list[Entity]:
        """Get entities by name."""
        return [e for e in self._entities.values() if e.name == name]

    @property
    def entities(self) -> list:
        return list(self._entities.values())

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    # ── Relationship Operations ─────────────────────────────────

    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship_type: RelationshipType = RelationshipType.RELATED_TO,
        attributes: dict | None = None,
        start_date: str = "",
        end_date: str = "",
        confidence: float = 1.0,
    ) -> Relationship:
        """Add a relationship between two entities."""
        from src.monkey_brain.kernel.security_boundary import (
            assert_state_mutation_allowed,
        )

        assert_state_mutation_allowed("knowledge_graph.add_relationship")
        import uuid

        rel = Relationship(
            relationship_id=str(uuid.uuid4()),
            source_id=source_id,
            target_id=target_id,
            relationship_type=relationship_type,
            attributes=attributes or {},
            start_date=start_date,
            end_date=end_date,
            confidence=confidence,
        )
        self._relationships[rel.relationship_id] = rel
        self._adjacency.setdefault(source_id, []).append(rel.relationship_id)
        self._reverse_adjacency.setdefault(target_id, []).append(rel.relationship_id)

        # Structured logging
        rel_type = relationship_type.value if hasattr(relationship_type, "value") else str(relationship_type)
        logger.info(
            "RELATIONSHIP_ADDED: %s --[%s]--> %s (id=%s)",
            source_id,
            rel_type,
            target_id,
            rel.relationship_id[:8],
        )
        self._notify_change("relationship", rel.relationship_id, "upsert")

        return rel

    def get_relationship(self, relationship_id: str) -> Relationship | None:
        """Get a relationship by ID."""
        return self._relationships.get(relationship_id)

    def remove_relationship(self, relationship_id: str) -> bool:
        """Remove a relationship."""
        from src.monkey_brain.kernel.security_boundary import (
            assert_state_mutation_allowed,
        )

        assert_state_mutation_allowed("knowledge_graph.remove_relationship")
        rel = self._relationships.pop(relationship_id, None)
        if rel is None:
            return False

        source_list = self._adjacency.get(rel.source_id, [])
        if relationship_id in source_list:
            source_list.remove(relationship_id)

        target_list = self._reverse_adjacency.get(rel.target_id, [])
        if relationship_id in target_list:
            target_list.remove(relationship_id)

        logger.info("RELATIONSHIP_REMOVED: %s (id=%s)", rel.source_id, relationship_id[:8])
        self._notify_change("relationship", relationship_id, "delete")

        return True

    def relationships_for(self, entity_id: str, direction: str = "both") -> list[Relationship]:
        """Get relationships involving an entity."""
        results = []

        if direction in ("outgoing", "both"):
            for rel_id in self._adjacency.get(entity_id, []):
                rel = self._relationships.get(rel_id)
                if rel and rel.source_id == entity_id:
                    results.append(rel)

        if direction in ("incoming", "both"):
            for rel_id in self._reverse_adjacency.get(entity_id, []):
                rel = self._relationships.get(rel_id)
                if rel and rel.target_id == entity_id:
                    results.append(rel)

        return results

    def relationships_by_type(self, relationship_type: RelationshipType) -> list[Relationship]:
        """Get all relationships of a specific type."""
        return [r for r in self._relationships.values() if r.relationship_type == relationship_type]

    def relationships_between(self, source_id: str, target_id: str) -> list[Relationship]:
        """Get all relationships between two entities."""
        return [r for r in self._relationships.values() if r.source_id == source_id and r.target_id == target_id]

    @property
    def relationship_count(self) -> int:
        return len(self._relationships)

    # ── Temporal Operations ─────────────────────────────────────

    def create_snapshot(self, year: int) -> TemporalSnapshot:
        """Create a temporal snapshot."""
        import uuid

        snapshot = TemporalSnapshot(
            snapshot_id=str(uuid.uuid4()),
            entity_id=self.person_id,
            year=year,
            attributes={e.entity_id: e.attributes for e in self._entities.values()},
            relationships=list(self._relationships.keys()),
        )
        self._snapshots.append(snapshot)
        return snapshot

    def get_snapshots(self, year: int | None = None) -> list[TemporalSnapshot]:
        """Get snapshots, optionally filtered by year."""
        if year:
            return [s for s in self._snapshots if s.year == year]
        return list(self._snapshots)

    def get_snapshot_diff(self, year1: int, year2: int) -> dict[str, Any]:
        """Compare two snapshots and find differences."""
        snap1 = next((s for s in self._snapshots if s.year == year1), None)
        snap2 = next((s for s in self._snapshots if s.year == year2), None)

        if not snap1 or not snap2:
            return {"error": "Snapshot not found"}

        return {
            "year1": year1,
            "year2": year2,
            "attributes_changed": {
                k: {"old": snap1.attributes.get(k), "new": snap2.attributes.get(k)}
                for k in set(list(snap1.attributes.keys()) + list(snap2.attributes.keys()))
                if snap1.attributes.get(k) != snap2.attributes.get(k)
            },
            "relationships_added": len(snap2.relationships) - len(snap1.relationships),
        }

    # ── Graph Traversal ─────────────────────────────────────────

    def find_path(self, source_id: str, target_id: str, max_hops: int = 5) -> list[Relationship]:
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

            for rel in self.relationships_for(current):
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
        for rel in self.relationships_for(entity_id):
            if rel.source_id == entity_id:
                connected.add(rel.target_id)
            else:
                connected.add(rel.source_id)
        return sorted(connected)

    # ── Domain-Specific Queries ─────────────────────────────────

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
        for rel in self._relationships.values():
            if rel.relationship_type in family_types:
                if rel.source_id == self.person_id:
                    family_ids.add(rel.target_id)
                elif rel.target_id == self.person_id:
                    family_ids.add(rel.source_id)
        return [self._entities[eid] for eid in family_ids if eid in self._entities]

    def get_employers(self) -> list[Entity]:
        """Get employers."""
        return [
            self._entities[r.target_id]
            for r in self._relationships.values()
            if r.source_id == self.person_id
            and r.relationship_type == RelationshipType.WORKS_FOR
            and r.target_id in self._entities
        ]

    def get_assets(self) -> list[Entity]:
        """Get owned assets."""
        return [
            self._entities[r.target_id]
            for r in self._relationships.values()
            if r.source_id == self.person_id
            and r.relationship_type in (RelationshipType.OWNS, RelationshipType.POSSESSES)
            and r.target_id in self._entities
        ]

    def get_investments(self) -> list[Entity]:
        """Get investments."""
        return [
            self._entities[r.target_id]
            for r in self._relationships.values()
            if r.source_id == self.person_id
            and r.relationship_type == RelationshipType.HOLDINGS
            and r.target_id in self._entities
        ]

    def get_income_sources(self) -> list[Entity]:
        """Get income sources."""
        return [
            self._entities[r.target_id]
            for r in self._relationships.values()
            if r.source_id == self.person_id
            and r.relationship_type == RelationshipType.RECEIVES_INCOME
            and r.target_id in self._entities
        ]

    def get_liabilities(self) -> list[Entity]:
        """Get liabilities (debts)."""
        return [
            self._entities[r.target_id]
            for r in self._relationships.values()
            if r.source_id == self.person_id
            and r.relationship_type == RelationshipType.PAYS_EXPENSE
            and r.target_id in self._entities
        ]

    def get_insurance(self) -> list[Entity]:
        """Get insurance policies."""
        return [
            self._entities[r.target_id]
            for r in self._relationships.values()
            if r.source_id == self.person_id
            and r.relationship_type == RelationshipType.INSURED_BY
            and r.target_id in self._entities
        ]

    def get_retirement_accounts(self) -> list[Entity]:
        """Get retirement accounts."""
        return [
            self._entities[r.target_id]
            for r in self._relationships.values()
            if r.source_id == self.person_id
            and r.relationship_type == RelationshipType.CONTRIBUTES_TO
            and r.target_id in self._entities
        ]

    def get_businesses(self) -> list[Entity]:
        """Get businesses."""
        return [
            self._entities[r.target_id]
            for r in self._relationships.values()
            if r.source_id == self.person_id
            and r.relationship_type == RelationshipType.OWNS_BUSINESS
            and r.target_id in self._entities
        ]

    # ── Statistics ──────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Get graph statistics."""
        type_counts = {}
        for e in self._entities.values():
            type_counts[e.entity_type.value] = type_counts.get(e.entity_type.value, 0) + 1

        rel_counts = {}
        for r in self._relationships.values():
            rel_counts[r.relationship_type.value] = rel_counts.get(r.relationship_type.value, 0) + 1

        return {
            "person_id": self.person_id,
            "entity_count": self.entity_count,
            "relationship_count": self.relationship_count,
            "snapshot_count": len(self._snapshots),
            "entity_types": type_counts,
            "relationship_types": rel_counts,
        }

    # ── Serialization ───────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "person_id": self.person_id,
            "entities": {k: v.to_dict() for k, v in self._entities.items()},
            "relationships": {k: v.to_dict() for k, v in self._relationships.items()},
            "snapshots": [s.to_dict() for s in self._snapshots],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeGraph:
        """Deserialize from dict."""
        kg = cls(person_id=data.get("person_id", ""))
        for eid, edata in data.get("entities", {}).items():
            kg._entities[eid] = Entity.from_dict(edata)
        for rid, rdata in data.get("relationships", {}).items():
            kg._relationships[rid] = Relationship.from_dict(rdata)
            rel = kg._relationships[rid]
            kg._adjacency.setdefault(rel.source_id, []).append(rid)
            kg._reverse_adjacency.setdefault(rel.target_id, []).append(rid)
        for sdata in data.get("snapshots", []):
            kg._snapshots.append(TemporalSnapshot.from_dict(sdata))
        return kg
