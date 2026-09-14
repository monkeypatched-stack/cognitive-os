"""Neo4jBackedKnowledgeGraph — Neo4j persistence for a single actor's KnowledgeGraph.

Subclasses KnowledgeGraph so every read (entities, entities_by_type,
relationships_for, ...) keeps working against the in-memory cache exactly as
before. Writes (add_entity, add_relationship) additionally MERGE the same
data into Neo4j, and construction hydrates the in-memory cache by reading
back everything already stored under this person_id. That makes the graph
survive a process restart, which the plain in-memory KnowledgeGraph cannot.

Connects with the sync neo4j driver (not AsyncGraphDatabase, used by
GraphStore in persistence/graph_store.py) because the grocery domain
capabilities that read kg.entities are synchronous handle() methods.

Every relationship is written as a single generic :KG_REL type carrying a
`relationship_type` property, rather than one Neo4j relationship type per
RelationshipType member (60+ of them) — Neo4j can't parameterize a
relationship type in Cypher, and interpolating a planner/LLM-influenced
enum name into the query string is the injection pattern
persistence/graph_store.py explicitly guards against.

## Household-shared entities

Every KGEntity node is keyed by (person_id, entity_id). Writing the "same"
entity_id into two different people's graphs (e.g. a household wallet added
to both alice's and jose's KG) does NOT share anything — it creates two
independent nodes that happen to start with equal data and then diverge the
moment either actor spends from it. Verified: alice's purchase debited her
copy, but jose's next purchase still read the original seeded balance
because his copy was never touched.

A household_id gives entities a THIRD partition, alongside the actor's own
person_id: entities tagged attributes.household = True are written under
household_id instead of person_id, and hydration merges in whatever's
already stored under household_id alongside the actor's personal entities —
so alice and jose, given the same household_id, see and mutate the exact
same underlying node.

## Public catalog entities (Level 48, GS-4800)

A store (EntityType.ORGANIZATION) or a product it stocks
(EntityType.ASSET with attributes.type == "product") isn't personal OR
even just household-shared data — it's the shared marketplace every
actor should see, regardless of which actor's own KG instance happened
to observe/create it first. Without a distinct scope for these, they
defaulted to whichever actor's person_id created them, and were
invisible to every OTHER actor's own separate Neo4jBackedKnowledgeGraph
instance — even a genuinely different household member's. Verified live
(cross-runtime synchronization test, two fully independent instances,
no shared Python object at all): alice's instance created the store and
its products; bob's own separate instance, sharing alice's household_id
but not her person_id, saw zero stores and zero products at all, and
could not buy anything. Every entity of these two kinds is now written
to — and every instance always hydrates from — a fixed, well-known
public scope (_PUBLIC_CATALOG_SCOPE), so the catalog itself is visible
regardless of which instance is asking.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from src.monkey_brain.kernel.knowledge_graph import (
    Entity,
    EntityType,
    KnowledgeGraph,
    Relationship,
    RelationshipType,
)

logger = logging.getLogger("agentos.knowledge_graph.neo4j")

_PUBLIC_CATALOG_SCOPE = "__catalog__"
"""Level 48 (GS-4800): the fixed, well-known partition stores and their
products are written to/hydrated from — every instance always includes
this scope, regardless of person_id/household_id, so the marketplace
itself is genuinely public rather than defaulting to whichever actor's
instance happened to create it first."""

_PUBLIC_MARKETPLACE_SCOPE = "__marketplace__"
"""Level 50 (GS-5000): a SEPARATE public partition for peer-to-peer
FOR-SALE listings — distinct from _PUBLIC_CATALOG_SCOPE (genuine store
products) and from ordinary household pantry stock (which must stay
private, per Level 46). A listing a neighbor explicitly offers for sale
(attributes["for_sale"] is True) is meant to be DISCOVERABLE by other
households' own separate runtime instances — that's the entire point of
"selling to a neighbor" (SocialSourcingCapability/negotiate_price) — but
ordinary household_stock pantry items (what's currently in MY fridge,
never offered to anyone) must remain scoped to person_id/household_id
exactly as Level 46 fixed, or a stranger's actor could falsely treat
someone else's private stock as their own household's supply again.
"""


def _is_public_catalog_entity(entity: Entity) -> bool:
    """A genuine STORE product only. grocery.py's own domain_security/
    pantry code reuses this same {"type": "product", ...} shape for
    PERSONAL household stock and peer-to-peer for-sale listings (Level
    16/46's find_borrowable/find_household_pantry_stock/SocialSourcing
    all key off attributes["pantry"], not entity_type) — those are
    handled by _is_public_marketplace_entity below instead, never here.
    """
    if entity.entity_type == EntityType.ORGANIZATION:
        return True
    return entity.attributes.get("type") == "product" and not entity.attributes.get("pantry")


def _is_public_marketplace_entity(entity: Entity) -> bool:
    """Level 50 (GS-5000): an explicit peer-to-peer for-sale listing —
    discoverable by any household, unlike ordinary pantry stock. Caught
    live in an end-to-end multi-household scenario run against a real
    database: correctly excluding pantry items from the public catalog
    scope (this fix's own prerequisite) made peer-to-peer discovery
    structurally impossible across genuinely separate runtime instances
    — a neighbor's for-sale listing had nowhere shared to live at all,
    so a real buyer in a different household could never find it. A
    plain household_stock pantry item (attributes["pantry"] is True but
    NOT for_sale) is deliberately excluded here — it stays scoped to
    person_id/household_id exactly as Level 46 fixed, since nothing about
    "what's currently in my fridge" should be visible to a stranger.
    """
    return entity.attributes.get("pantry") is True and entity.attributes.get("for_sale") is True


# One driver per (uri, user, password), shared by every Neo4jBackedKnowledgeGraph
# instance for the life of the process. _get_actor_kg (routes/prompt.py,
# routes/knowledge_graph.py) constructs a fresh instance on EVERY request —
# opening a brand-new GraphDatabase.driver() there and never closing it leaked
# a connection pool per request, and under this session's request volume that
# eventually made verify_connectivity() fail intermittently, so an actor's KG
# would randomly come back 404 even though the data was in Neo4j the whole
# time. A shared, reused driver is the standard neo4j-driver usage pattern
# (it's thread-safe and meant to be long-lived) and removes the leak entirely.
_driver_cache: dict[tuple[str, str, str], Any] = {}
_driver_lock = threading.Lock()


def _get_shared_driver(uri: str, user: str, password: str):
    key = (uri, user, password)
    driver = _driver_cache.get(key)
    if driver is not None:
        return driver
    with _driver_lock:
        driver = _driver_cache.get(key)
        if driver is None:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(uri, auth=(user, password))
            driver.verify_connectivity()
            _driver_cache[key] = driver
        return driver


def _default_driver():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD") or ""
    if not password:
        return None
    try:
        return _get_shared_driver(uri, user, password)
    except Exception as exc:
        logger.warning("Neo4j driver unavailable for household lookup (%s)", exc)
        return None


def resolve_household_id(person_id: str) -> str | None:
    """The household this person belongs to, or None if they aren't in one.

    Membership lives as its own tiny node type (:HouseholdMember), separate
    from KGEntity, so looking it up doesn't require hydrating anyone's full
    graph first.
    """
    driver = _default_driver()
    if driver is None:
        return None
    try:
        with driver.session() as session:
            record = session.run(
                "MATCH (m:HouseholdMember {person_id: $pid}) RETURN m.household_id AS household_id LIMIT 1",
                pid=person_id,
            ).single()
            return record["household_id"] if record else None
    except Exception as exc:
        logger.warning("Household lookup failed for %s (%s)", person_id, exc)
        return None


def set_household_membership(person_id: str, household_id: str) -> None:
    """Register person_id as a member of household_id."""
    driver = _default_driver()
    if driver is None:
        raise RuntimeError("Neo4j unavailable — cannot register household membership")
    with driver.session() as session:
        session.run(
            "MERGE (m:HouseholdMember {person_id: $pid}) SET m.household_id = $hid",
            pid=person_id,
            hid=household_id,
        )


def _household_role_entity_id(person_id: str) -> str:
    return f"household_role_{person_id}"


def set_household_role(person_id: str, household_id: str, role: str, kg: Any = None) -> None:
    """Level 37 (GS-3700/3701): registers person_id's real governance role
    ("owner" or "member") within household_id — only "owner" may create or
    modify household policies (Level 36). Setting a role also establishes
    membership, same as set_household_membership.

    kg: when Neo4j is unavailable, the role is instead recorded as a real
    KG entity (person_id's own graph, EntityType.OTHER,
    attributes["household_member"]=True) so this governance check is
    exercisable without a live Neo4j instance — the same portability
    reasoning as KnowledgeGraph.compare_and_swap/version_of/refresh. This
    is a genuine record, not a mock: household_role() reads it back the
    same way it reads a real Neo4j HouseholdMember node.
    """
    driver = _default_driver()
    if driver is None:
        if kg is None:
            raise RuntimeError("Neo4j unavailable — cannot register household role")
        from src.monkey_brain.kernel.knowledge_graph import EntityType

        kg.add_entity(
            _household_role_entity_id(person_id),
            EntityType.OTHER,
            f"Household role: {person_id}",
            {
                "household_member": True,
                "person_id": person_id,
                "household_id": household_id,
                "role": role,
            },
        )
        return
    with driver.session() as session:
        session.run(
            "MERGE (m:HouseholdMember {person_id: $pid}) SET m.household_id = $hid, m.role = $role",
            pid=person_id,
            hid=household_id,
            role=role,
        )


def household_role(person_id: str, kg: Any = None) -> str:
    """person_id's real governance role — "owner" or "member". Defaults to
    "member" for anyone never explicitly granted "owner", including every
    household member registered before Level 37 existed (set_household_membership
    never wrote a role property at all, so it reads back as None here).

    kg: see set_household_role's docstring — the same in-memory fallback,
    read back here when Neo4j is unavailable.
    """
    driver = _default_driver()
    if driver is None:
        if kg is not None:
            entity = kg.get_entity(_household_role_entity_id(person_id))
            if entity is not None and entity.attributes.get("household_member"):
                return entity.attributes.get("role") or "member"
        return "member"
    try:
        with driver.session() as session:
            record = session.run(
                "MATCH (m:HouseholdMember {person_id: $pid}) RETURN m.role AS role LIMIT 1",
                pid=person_id,
            ).single()
            return record["role"] if record and record["role"] else "member"
    except Exception as exc:
        logger.warning("Household role lookup failed for %s (%s)", person_id, exc)
        return "member"


def _org_role_entity_id(person_id: str, org_id: str) -> str:
    return f"org_role_{person_id}_{org_id}"


def set_org_role(person_id: str, org_id: str, role: str, kg: Any = None) -> None:
    """Level 40 (GS-4000/4001/4002): registers person_id's real enterprise
    role ("staff", "procurement_manager", or "admin") within a specific
    org_id. A SEPARATE node type from HouseholdMember/household_role —
    reusing that would collide the moment someone (like alice, already a
    real household "owner") also needed an enterprise role, since
    HouseholdMember assumes one household per person. OrgMember is keyed
    on (person_id, org_id) so one person can hold different real roles in
    different organizations at once.

    kg: same in-memory fallback as set_household_role, keyed on
    (person_id, org_id) to mirror OrgMember's own key.
    """
    driver = _default_driver()
    if driver is None:
        if kg is None:
            raise RuntimeError("Neo4j unavailable — cannot register org role")
        from src.monkey_brain.kernel.knowledge_graph import EntityType

        kg.add_entity(
            _org_role_entity_id(person_id, org_id),
            EntityType.OTHER,
            f"Org role: {person_id}/{org_id}",
            {
                "org_member": True,
                "person_id": person_id,
                "org_id": org_id,
                "role": role,
            },
        )
        return
    with driver.session() as session:
        session.run(
            "MERGE (m:OrgMember {person_id: $pid, org_id: $oid}) SET m.role = $role",
            pid=person_id,
            oid=org_id,
            role=role,
        )


def org_role(person_id: str, org_id: str, kg: Any = None) -> str:
    """person_id's real enterprise role within org_id — "staff" (the
    lowest-privilege real role) for anyone never explicitly assigned one
    in THIS org, rather than household_role's "member" default, since an
    enterprise account has no such default relationship at all.

    kg: same in-memory fallback as household_role."""
    driver = _default_driver()
    if driver is None:
        if kg is not None:
            entity = kg.get_entity(_org_role_entity_id(person_id, org_id))
            if entity is not None and entity.attributes.get("org_member"):
                return entity.attributes.get("role") or "staff"
        return "staff"
    try:
        with driver.session() as session:
            record = session.run(
                "MATCH (m:OrgMember {person_id: $pid, org_id: $oid}) RETURN m.role AS role LIMIT 1",
                pid=person_id,
                oid=org_id,
            ).single()
            return record["role"] if record and record["role"] else "staff"
    except Exception as exc:
        logger.warning("Org role lookup failed for %s/%s (%s)", person_id, org_id, exc)
        return "staff"


class Neo4jBackedKnowledgeGraph(KnowledgeGraph):
    def __init__(
        self,
        person_id: str = "",
        household_id: str | None = None,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ):
        super().__init__(person_id=person_id)
        self._household_id = household_id
        self._uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self._user = user or os.getenv("NEO4J_USER", "neo4j")
        self._password = password or os.getenv("NEO4J_PASSWORD") or ""
        self._driver = None
        self._available = False
        # Which partition (person_id or household_id) each hydrated/written
        # entity actually lives under — update_entity needs this to write
        # back to the same node it read from, not silently re-partition it.
        self._entity_scope: dict[str, str] = {}
        # Optimistic-concurrency version per entity (see compare_and_swap) —
        # a plain top-level Neo4j integer property, not part of the
        # attributes_json blob, so Cypher can condition a write on it
        # without needing to parse JSON.
        self._entity_version: dict[str, int] = {}
        self._connect()
        if self._available:
            self._load_from_neo4j()

    # ── Connection ──────────────────────────────────────────────────────

    def _connect(self) -> None:
        if not self._password:
            logger.warning("Neo4jBackedKnowledgeGraph disabled: NEO4J_PASSWORD is unset")
            return
        try:
            self._driver = _get_shared_driver(self._uri, self._user, self._password)
            self._available = True
        except Exception as exc:
            logger.warning(
                "Neo4jBackedKnowledgeGraph connect failed (%s) — falling back to in-memory only",
                exc,
            )
            self._driver = None
            self._available = False

    def close(self) -> None:
        """No-op: the underlying driver is shared process-wide (see
        _get_shared_driver) and outlives any single KG instance."""

    # ── Hydration ───────────────────────────────────────────────────────

    def _scopes(self) -> list[str]:
        scopes = [self.person_id]
        if self._household_id:
            scopes.append(self._household_id)
        # Level 48/50 (GS-4800/5000): always hydrate the public catalog
        # AND public marketplace scopes too, regardless of person_id/
        # household_id — the store catalog and peer-to-peer for-sale
        # listings both need to be genuinely discoverable, unlike
        # ordinary personal/household pantry stock.
        scopes.append(_PUBLIC_CATALOG_SCOPE)
        scopes.append(_PUBLIC_MARKETPLACE_SCOPE)
        return scopes

    def _load_from_neo4j(self) -> None:
        with self._driver.session() as session:
            for scope in self._scopes():
                for record in session.run(
                    "MATCH (n:KGEntity {person_id: $pid}) RETURN n",
                    pid=scope,
                ):
                    n = record["n"]
                    # A node can exist with only (person_id, entity_id) set — e.g. an
                    # endpoint auto-created by add_relationship's MERGE before
                    # add_entity ever ran for it (fixed to set defaults going
                    # forward, but old data or a relationship added standalone can
                    # still hit this). Degrade to OTHER/blank rather than raising
                    # ValueError("None is not a valid EntityType") and losing the
                    # whole graph for this actor.
                    entity = Entity(
                        entity_id=n["entity_id"],
                        entity_type=EntityType(n.get("entity_type") or "other"),
                        name=n.get("name") or "",
                        attributes=json.loads(n.get("attributes_json") or "{}"),
                    )
                    self._entities[entity.entity_id] = entity
                    self._index_add(entity)
                    self._entity_scope[entity.entity_id] = scope
                    self._entity_version[entity.entity_id] = n.get("version", 0)

            for record in session.run(
                "MATCH (s:KGEntity {person_id: $pid})-[r:KG_REL]->(t:KGEntity {person_id: $pid}) "
                "RETURN s.entity_id AS source_id, t.entity_id AS target_id, r",
                pid=self.person_id,
            ):
                r = record["r"]
                rel = Relationship(
                    relationship_id=r["relationship_id"],
                    source_id=record["source_id"],
                    target_id=record["target_id"],
                    relationship_type=RelationshipType(r.get("relationship_type") or "RELATED_TO"),
                    attributes=json.loads(r.get("attributes_json") or "{}"),
                    confidence=r.get("confidence", 1.0),
                )
                self._relationships[rel.relationship_id] = rel
                self._adjacency.setdefault(rel.source_id, []).append(rel.relationship_id)
                self._reverse_adjacency.setdefault(rel.target_id, []).append(rel.relationship_id)

        logger.info(
            "Neo4jBackedKnowledgeGraph[%s household=%s]: hydrated %d entities, %d relationships",
            self.person_id,
            self._household_id,
            len(self._entities),
            len(self._relationships),
        )

    def refresh(self) -> None:
        """Discard the in-memory snapshot and re-hydrate from Neo4j.

        GS-0500/0501/0502: a capability that wants the world's CURRENT
        state (price, stock, whether a store is still open) rather than
        what was true when this KG instance was first constructed calls
        this. Without it, every read — including OrderConfirmation's
        stale-data check — was comparing against the SAME in-memory
        snapshot the original pick came from, which can never detect a
        real change; it can only ever agree with itself.
        """
        if not self._available:
            return
        self._entities = {}
        self._entities_by_type = {}
        self._entity_keywords = {}
        self._keyword_index = {}
        self._relationships = {}
        self._adjacency = {}
        self._reverse_adjacency = {}
        self._entity_scope = {}
        self._entity_version = {}
        self._load_from_neo4j()

    # ── Writes (in-memory cache via super(), then MERGE into Neo4j) ─────

    def _scope_for(self, entity: Entity) -> str:
        """The public catalog scope for stores/products (Level 48,
        GS-4800), the public marketplace scope for peer-to-peer for-sale
        listings (Level 50, GS-5000), else household_id for entities
        explicitly tagged shared, else the actor's own person_id. An
        entity already hydrated from a scope keeps that scope on update,
        regardless of its current attributes."""
        existing = self._entity_scope.get(entity.entity_id)
        if existing:
            return existing
        if _is_public_catalog_entity(entity):
            return _PUBLIC_CATALOG_SCOPE
        if _is_public_marketplace_entity(entity):
            return _PUBLIC_MARKETPLACE_SCOPE
        if self._household_id and entity.attributes.get("household") is True:
            return self._household_id
        return self.person_id

    def _write_entity(self, entity: Entity) -> None:
        if not self._available:
            return
        scope = self._scope_for(entity)
        self._entity_scope[entity.entity_id] = scope
        with self._driver.session() as session:
            record = session.run(
                "MERGE (n:KGEntity {person_id: $pid, entity_id: $eid}) "
                "SET n.entity_type = $etype, n.name = $name, n.attributes_json = $attrs, "
                "n.version = coalesce(n.version, 0) + 1 "
                "RETURN n.version AS version",
                pid=scope,
                eid=entity.entity_id,
                etype=entity.entity_type.value,
                name=entity.name,
                attrs=json.dumps(entity.attributes),
            ).single()
        self._entity_version[entity.entity_id] = record["version"] if record else 0

    def version_of(self, entity_id: str) -> int:
        """Last known version for entity_id — the value to pass as
        expected_version to compare_and_swap. 0 if never seen (matches a
        freshly-created node's coalesce(n.version, 0) + 1 = 1 after its
        first write, so a caller racing to create-then-claim in one motion
        still has a sane number to start from)."""
        return self._entity_version.get(entity_id, 0)

    def compare_and_swap(
        self, entity_id: str, expected_version: int, attribute_updates: dict
    ) -> tuple[bool, Entity | None]:
        """Atomically merge attribute_updates into entity_id's attributes
        ONLY if its Neo4j version still equals expected_version.

        GS-0600: two actors racing for the last unit of stock both read
        quantity=1, both decide they can buy it, both write quantity=0 —
        two orders fulfilled from one unit, because a plain read-then-write
        has no way to notice the other write happened in between. version
        is a real top-level Neo4j property (not buried in the
        attributes_json blob, which Cypher can't condition a WHERE on), so
        the MATCH here is a genuine atomic compare-and-swap: the database,
        not this process, decides whether the expected version still holds.

        Returns (True, updated_entity) on success. Returns (False,
        current_entity) on conflict — current_entity is freshly re-fetched
        (and the local cache updated to match), so a retry loop sees what
        actually won the race instead of retrying against its own stale
        guess.
        """
        if not self._available:
            return False, None
        from src.monkey_brain.kernel.security_boundary import (
            assert_state_mutation_allowed,
        )

        assert_state_mutation_allowed("knowledge_graph.compare_and_swap")
        entity = self._entities.get(entity_id)
        if entity is None:
            return False, None
        scope = self._entity_scope.get(entity_id, self.person_id)
        merged = {**entity.attributes, **attribute_updates}

        def _cas_tx(tx):
            # Three things were tried here before this worked, each caught
            # by a real two-thread test racing for the literal last unit of
            # stock (not reasoned about, empirically observed):
            #  1. MATCH (n {version: $expected}) — filters by the mutable
            #     property before Neo4j takes any write lock, so two
            #     concurrent transactions can both match the same
            #     pre-conflict version and both proceed to write.
            #  2. WITH n, (n.version = $expected) AS matched, then a
            #     conditional SET — the boolean is still computed in a read
            #     projection before the lock, same bug relocated.
            #  3. SET n.version = n.version to force a lock before
            #     comparing — a diagnostic run showed BOTH threads reading
            #     version=1 with no serialization at all. Neo4j's planner
            #     evidently recognizes a self-assignment as a no-op and
            #     doesn't bother taking a write lock for it.
            # What actually serializes (confirmed with the same two-thread
            # harness): a lock-forcing write that GENUINELY changes a value.
            # lock_seq is bumped solely to force a real write lock; version
            # is read in that same locked statement, so by the time this
            # transaction sees it, no concurrent transaction can be mid-write
            # on this node — it's either fully committed or still queued
            # behind this transaction's lock.
            locked = tx.run(
                "MATCH (n:KGEntity {person_id: $pid, entity_id: $eid}) "
                "SET n.lock_seq = coalesce(n.lock_seq, 0) + 1 "
                "RETURN n.version AS version",
                pid=scope,
                eid=entity_id,
            ).single()
            if locked is None or locked["version"] != expected_version:
                return None
            return tx.run(
                "MATCH (n:KGEntity {person_id: $pid, entity_id: $eid}) "
                "SET n.attributes_json = $attrs, n.version = $expected + 1 "
                "RETURN n.version AS version",
                pid=scope,
                eid=entity_id,
                expected=expected_version,
                attrs=json.dumps(merged),
            ).single()

        with self._driver.session() as session:
            record = session.execute_write(_cas_tx)

        if record is not None:
            entity.attributes.update(attribute_updates)
            self._entities[entity_id] = entity
            self._entity_version[entity_id] = record["version"]
            return True, entity

        # Lost the race (or expected_version was wrong to begin with) — fetch
        # what's actually there now rather than leaving the cache stale.
        with self._driver.session() as session:
            fresh = session.run(
                "MATCH (n:KGEntity {person_id: $pid, entity_id: $eid}) RETURN n",
                pid=scope,
                eid=entity_id,
            ).single()
        if fresh is None:
            return False, None
        n = fresh["n"]
        current = Entity(
            entity_id=n["entity_id"],
            entity_type=EntityType(n.get("entity_type") or "other"),
            name=n.get("name") or "",
            attributes=json.loads(n.get("attributes_json") or "{}"),
        )
        self._entities[entity_id] = current
        # current is a freshly-built Entity object, not the same instance
        # the type index bucket was holding — refresh that reference too,
        # or entities_by_type() would keep returning the stale pre-race
        # object even though self._entities already has the real one.
        self._entities_by_type.setdefault(current.entity_type, {})[entity_id] = current
        self._entity_version[entity_id] = n.get("version", 0)
        return False, current

    def add_entity(
        self,
        entity_id: str,
        entity_type: EntityType = EntityType.OTHER,
        name: str = "",
        attributes: dict | None = None,
        **kwargs,
    ) -> Entity:
        entity = super().add_entity(entity_id, entity_type, name, attributes, **kwargs)
        self._write_entity(entity)
        return entity

    def update_entity(self, entity_id: str, **kwargs) -> Entity | None:
        """Persist an in-place entity mutation to Neo4j, to the same
        partition (personal or household) the entity was read from.

        The base KnowledgeGraph.update_entity only mutates the in-memory
        Entity — fine for the plain in-memory graph, but here it silently
        stranded writes: PaymentCapability set wallet.attributes["balance"]
        directly and nothing ever wrote it back, so every fresh
        Neo4jBackedKnowledgeGraph (i.e. every request) re-hydrated the
        original seeded balance. That was invisible with one actor per
        wallet; it became visibly wrong once a wallet was shared between
        two actors — each spender's purchase reset from the same starting
        balance instead of compounding.
        """
        entity = super().update_entity(entity_id, **kwargs)
        if entity is not None:
            self._write_entity(entity)
        return entity

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
        rel = super().add_relationship(
            source_id,
            target_id,
            relationship_type,
            attributes,
            start_date,
            end_date,
            confidence,
        )
        if self._available:
            with self._driver.session() as session:
                session.run(
                    # ON CREATE SET gives a freshly auto-vivified endpoint (e.g. the
                    # actor's own entity_id, referenced as a relationship source
                    # before it was ever add_entity'd) valid entity_type/name/
                    # attributes_json immediately — a bare {person_id, entity_id}
                    # node previously slipped through here with entity_type
                    # unset, which crashed hydration with
                    # ValueError("None is not a valid EntityType") on every
                    # subsequent load of this actor's graph.
                    "MERGE (s:KGEntity {person_id: $pid, entity_id: $sid}) "
                    "ON CREATE SET s.entity_type = 'other', s.name = $sid, s.attributes_json = '{}' "
                    "MERGE (t:KGEntity {person_id: $pid, entity_id: $tid}) "
                    "ON CREATE SET t.entity_type = 'other', t.name = $tid, t.attributes_json = '{}' "
                    "MERGE (s)-[r:KG_REL {relationship_id: $rid}]->(t) "
                    "SET r.relationship_type = $rtype, r.attributes_json = $attrs, r.confidence = $conf",
                    pid=self.person_id,
                    sid=source_id,
                    tid=target_id,
                    rid=rel.relationship_id,
                    rtype=rel.relationship_type.value,
                    attrs=json.dumps(rel.attributes),
                    conf=rel.confidence,
                )
        return rel
