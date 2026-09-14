"""Gate 4 — unit tests for kernel/validation/world_validator.py (Gate 3/ADR-010).

One test per category, each constructing the SMALLEST possible
PlanetaryRuntime that exercises exactly that category's logic, and
asserting relative to a specifically-created id — never "assert the
world is globally clean" (ADR-010 documents why: kernel/timeline's
TimelineStore defaults to a real, shared Redis backend in dev
environments that have one reachable; TIMELINE_STORE_BACKEND=memory
below forces a clean, isolated, in-process store for these tests
specifically, but relative assertions are kept anyway since this env
var only takes effect if set before ANY test in the pytest session
constructs the first PlanetaryRuntime — not something a single test
file can guarantee across a whole suite run).

Several tests deliberately bypass the normal, validated write paths
(GeographicRegistry.create(), PresenceTimeline.move_actor()) via lower-
level registry methods (register(), TimelineStore.close()) — this is
the correct way to construct these scenarios: create() and move_actor()
already REFUSE to produce an orphan/tier-violation/presence-less actor
by design (confirmed while writing this file), so the validator's checks
are defense-in-depth against whatever bypasses those paths (direct
persistence restore, data migration, a future write path with a bug),
not scenarios reachable through the normal, already-guarded APIs.
"""

from __future__ import annotations

import os
import time
import uuid

os.environ.setdefault("TIMELINE_STORE_BACKEND", "memory")

from src.monkey_brain.kernel.geography.entity import (
    Building,
    City,
    GeographicEntityType,
)  # noqa: E402
from src.monkey_brain.kernel.society.domain import (  # noqa: E402
    ActorIdentity,
    ActorProfile,
    ActorType,
    Society,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime  # noqa: E402
from src.monkey_brain.kernel.society.world import (
    WorldEntity,
    WorldEntityType,
    WorldEvent,
    WorldRelationship,
)  # noqa: E402
from src.monkey_brain.kernel.timeline.entry import TimelineKind  # noqa: E402
from src.monkey_brain.kernel.validation.world_validator import (
    validate_world,
)  # noqa: E402


def _pr() -> PlanetaryRuntime:
    return PlanetaryRuntime(Society(name=f"unit-test-{uuid.uuid4().hex[:8]}"))


def _fresh_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


# ── 1 & 8. geography_tree / cycles_forbidden ─────────────────────────────


def test_orphaned_geography_entity_is_caught():
    pr = _pr()
    orphan = Building(name="Orphan Building", parent_id="does-not-exist")
    pr._geo_registry.register(orphan)

    report = validate_world(pr)
    assert any(
        v["category"] == "geography_tree"
        and v["type"] == "orphaned_geographic_entity"
        and v["entity_id"] == orphan.entity_id
        for v in report["violations"]
    )


def test_geography_tier_violation_is_caught():
    """A Building whose parent is a City (skipping Street) — create()
    already refuses this at write time, so this constructs it directly
    via register() to prove the validator catches it as defense-in-depth."""
    pr = _pr()
    planet = next(e for e in pr.geo_registry.all() if e.entity_type == GeographicEntityType.PLANET)
    city = City(name="Skip-Tier City", parent_id=planet.entity_id)
    pr._geo_registry.register(city)

    # The validator's tier check only reads each entity's own parent_id
    # (kernel/validation/world_validator.py::_check_geography_tree) — it
    # doesn't need child_ids linked back, so add_child() (which enforces
    # the very tier pairing this test needs to bypass) is deliberately
    # skipped here.
    building = Building(name="Skip-Tier Building", parent_id=city.entity_id)
    pr._geo_registry.register(building)

    report = validate_world(pr)
    assert any(
        v["category"] == "geography_tree"
        and v["type"] == "geography_tier_violation"
        and v["entity_id"] == building.entity_id
        for v in report["violations"]
    )


def test_geography_cycle_is_caught():
    pr = _pr()
    a = Building(name="A")
    b = Building(name="B", parent_id=a.entity_id)
    pr._geo_registry.register(a)
    pr._geo_registry.register(b)
    # Close the loop: A's parent is B (a dataclasses.replace-style direct
    # mutation via re-registration, since GeographicEntity is frozen).
    import dataclasses

    pr._geo_registry.register(dataclasses.replace(a, parent_id=b.entity_id))

    report = validate_world(pr)
    assert any(
        v["category"] == "cycles_forbidden"
        and v["type"] == "geography_cycle"
        and v["entity_id"] in (a.entity_id, b.entity_id)
        for v in report["violations"]
    )


# ── 2. society_hierarchy ─────────────────────────────────────────────────


def test_society_without_space_is_caught():
    from src.monkey_brain.kernel.society.runtime import SocietyRuntime

    pr = _pr()
    homeless = SocietyRuntime(Society(name="Homeless Society"))
    pr.add_society(homeless)  # add_society() auto-hosts at the default City
    host = pr._geo_registry.entity_for_society(homeless.society.society_id)
    assert host is not None
    pr._geo_registry.unhost_society(host.entity_id, homeless.society.society_id)

    report = validate_world(pr)
    assert any(
        v["category"] == "society_hierarchy"
        and v["type"] == "society_without_space"
        and v["society_id"] == homeless.society.society_id
        for v in report["violations"]
    )


# ── 3. presence_consistency ───────────────────────────────────────────────


def test_actor_without_presence_is_caught():
    pr = _pr()
    profile = ActorProfile(identity=ActorIdentity(name="NoPresence", actor_type=ActorType.HUMAN))
    state = pr.register_actor(profile)

    # Close the actor's open Presence record without opening a new one —
    # move_actor() (the only normal write path) always opens a new one,
    # so this uses TimelineStore.close() directly, the one sanctioned
    # "close an open interval" primitive, to reproduce "actor exists, no
    # current location."
    current = pr.presence.current(state.actor_id)
    assert current is not None and current.is_open()
    pr.presence._store.close(current, TimelineKind.PRESENCE, time.time())

    report = validate_world(pr)
    assert any(
        v["category"] == "presence_consistency"
        and v["type"] == "actor_without_presence"
        and v["actor_id"] == state.actor_id
        for v in report["violations"]
    )


# ── 4. membership_consistency ─────────────────────────────────────────────


def test_membership_referencing_unknown_actor_is_caught():
    pr = _pr()
    society_id = pr._society_runtime.society.society_id
    fake_actor_id = _fresh_id("nonexistent-actor")

    record = pr.membership_registry.add(fake_actor_id, society_id, role="member")

    report = validate_world(pr)
    assert any(
        v["category"] == "membership_consistency"
        and v["type"] == "membership_invalid_actor"
        and v["actor_id"] == fake_actor_id
        and v["membership_id"] == record.membership_id
        for v in report["violations"]
    )


def test_duplicate_active_membership_is_caught():
    pr = _pr()
    profile = ActorProfile(identity=ActorIdentity(name="DoubleMember", actor_type=ActorType.HUMAN))
    state = pr.register_actor(profile)
    society_id = pr._society_runtime.society.society_id

    # register_actor() already creates one PERMANENT membership; force a
    # second, independent active record for the same (actor, society) pair
    # directly on the timeline (bypassing add()'s own "already exists"
    # short-circuit) to reproduce genuine duplication.
    pr.membership_registry._store.record(
        TimelineKind.MEMBERSHIP,
        actor_id=state.actor_id,
        society_id=society_id,
        membership_id=_fresh_id("dup-membership"),
        roles=("member",),
        status="active",
    )

    report = validate_world(pr)
    assert any(
        v["category"] == "membership_consistency"
        and v["type"] == "duplicate_active_membership"
        and v["actor_id"] == state.actor_id
        for v in report["violations"]
    )


# ── 5. inventory_consistency ──────────────────────────────────────────────


def test_negative_product_quantity_is_caught():
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    pr = _pr()
    product_id = _fresh_id("product")
    pr.knowledge_graph.add_entity(
        product_id,
        EntityType.OTHER,
        "Bad Product",
        {
            "product": True,
            "store_id": "store-x",
            "price": 9.99,
            "quantity": -5,
        },
    )

    report = validate_world(pr)
    assert any(
        v["category"] == "inventory_consistency"
        and v["type"] == "negative_product_quantity"
        and v["product_id"] == product_id
        for v in report["violations"]
    )


def test_reservation_exceeding_quantity_is_caught():
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    pr = _pr()
    product_id = _fresh_id("product")
    pr.knowledge_graph.add_entity(
        product_id,
        EntityType.OTHER,
        "Overheld Product",
        {
            "product": True,
            "store_id": "store-x",
            "price": 9.99,
            "quantity": 5,
            "held_quantity": 50,
        },
    )

    report = validate_world(pr)
    assert any(
        v["category"] == "inventory_consistency"
        and v["type"] == "reservation_exceeds_quantity"
        and v["product_id"] == product_id
        for v in report["violations"]
    )


# ── 6. graph_integrity ────────────────────────────────────────────────────


def test_dangling_world_relationship_is_caught():
    pr = _pr()
    real_entity = WorldEntity(name="Real Entity", entity_type=WorldEntityType.RESOURCE)
    pr.world.add_entity(real_entity)
    rel = WorldRelationship(source_id=real_entity.entity_id, target_id=_fresh_id("missing-target"))
    pr.world.add_relationship(rel)

    report = validate_world(pr)
    assert any(
        v["category"] == "graph_integrity"
        and v["type"] == "relationship_target_missing"
        and v["relationship_id"] == rel.relationship_id
        for v in report["violations"]
    )


# ── 7. orphaned_nodes ──────────────────────────────────────────────────────


def test_world_event_referencing_unknown_entity_is_caught():
    pr = _pr()
    event = WorldEvent(entity_id=_fresh_id("nowhere"), description="orphan event")
    pr.world.record_event(event)

    report = validate_world(pr)
    assert any(
        v["category"] == "orphaned_nodes"
        and v["type"] == "event_references_missing_entity"
        and v["event_id"] == event.event_id
        for v in report["violations"]
    )


def test_world_event_referencing_a_real_actor_is_not_flagged():
    """The false-positive this engine specifically fixed during Gate 3 live
    verification: WorldEvent.entity_id legitimately references an Actor in
    existing domain code, not only a WorldEntity."""
    pr = _pr()
    profile = ActorProfile(identity=ActorIdentity(name="EventSubject", actor_type=ActorType.HUMAN))
    state = pr.register_actor(profile)
    event = WorldEvent(entity_id=state.actor_id, description="actor-scoped event")
    pr.world.record_event(event)

    report = validate_world(pr)
    assert not any(
        v["type"] == "event_references_missing_entity" and v.get("entity_id") == state.actor_id
        for v in report["violations"]
    )


# ── 9. duplicate_identifiers ──────────────────────────────────────────────


def test_id_reused_across_namespaces_is_caught():
    pr = _pr()
    collision_id = _fresh_id("collision")
    profile = ActorProfile(identity=ActorIdentity(actor_id=collision_id, name="Collider", actor_type=ActorType.HUMAN))
    pr.register_actor(profile)
    pr.world.add_entity(WorldEntity(entity_id=collision_id, name="Colliding World Entity"))

    report = validate_world(pr)
    assert any(
        v["category"] == "duplicate_identifiers"
        and v["type"] == "id_reused_across_namespaces"
        and v["entity_id"] == collision_id
        for v in report["violations"]
    )


# ── 10. referential_integrity ─────────────────────────────────────────────


def test_shipment_referencing_missing_order_is_caught():
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    pr = _pr()
    shipment_id = _fresh_id("shipment")
    pr.knowledge_graph.add_entity(
        shipment_id,
        EntityType.OTHER,
        "Orphan Shipment",
        {
            "shipment": True,
            "order_id": _fresh_id("missing-order"),
            "packages": [],
        },
    )

    report = validate_world(pr)
    assert any(
        v["category"] == "referential_integrity"
        and v["type"] == "shipment_references_missing_order"
        and v["shipment_id"] == shipment_id
        for v in report["violations"]
    )


def test_order_referencing_missing_product_is_caught():
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    pr = _pr()
    order_id = _fresh_id("order")
    pr.knowledge_graph.add_entity(
        order_id,
        EntityType.EVENT,
        "Order",
        {
            "order_id": order_id,
            "items": [{"id": _fresh_id("missing-product"), "qty": 1}],
            "total": 9.99,
            "status": "confirmed",
        },
    )

    report = validate_world(pr)
    assert any(
        v["category"] == "referential_integrity"
        and v["type"] == "order_references_missing_product"
        and v["order_id"] == order_id
        for v in report["violations"]
    )


# ── One raising check must not suppress the other nine ───────────────────


def test_one_category_raising_does_not_suppress_others(monkeypatch):
    import src.monkey_brain.kernel.validation.world_validator as wv

    def _boom(pr, violations):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(wv, "_check_geography_tree", _boom)

    pr = _pr()
    profile = ActorProfile(identity=ActorIdentity(name="StillChecked", actor_type=ActorType.HUMAN))
    state = pr.register_actor(profile)
    current = pr.presence.current(state.actor_id)
    pr.presence._store.close(current, TimelineKind.PRESENCE, time.time())

    report = validate_world(pr)
    assert any(v["category"] == "geography_tree" and v["type"] == "validator_error" for v in report["violations"])
    assert any(
        v["category"] == "presence_consistency" and v["actor_id"] == state.actor_id for v in report["violations"]
    )
