"""MB-3052 Society Coordination — verify all participating actors are
coordinated by the appropriate societies.

Examples: Marketplace Society, Merchant Society, Warehouse Society,
Logistics Society, Payment Society.

Investigation found: MB-3000 (Customer Registration) and MB-3001
(Merchant Registration) are the only participant types actually
coordinated by a real Society — every warehouse, delivery rider, and
payment processor used throughout the MB-3019-3050 domain layer
(logistics.py, supply_chain.py, finance.py) is a plain KG
ORGANIZATION/PERSON entity with attributes, never registered as a real
actor via PlanetaryRuntime.register_actor(). "Warehouse Society"/
"Logistics Society"/"Payment Society" didn't exist anywhere in the
product code — only referenced in an old, unrelated sprint test as a
generic API example.

Per explicit design choice ("register domain entities as real
actors"): this file registers one representative participant into each
of the 5 named societies via the SAME canonical registration workflow
MB-3000/MB-3001 already use (PlanetaryRuntime.create_society() +
register_actor(society_id=...)) — no new production code was needed,
since the Registration Entry Points refactor already generalized
register_actor() to any managed society. What was missing was simply
that nothing had ever exercised it for these 5 domains. Each
participant is verified against the full Actor Registration Invariant
(home society hosted at a real space, presence initialized, effective
membership immediately computable) MB-3000 established, and the file's
final test proves all 5 societies coexist without cross-contaminating
each other's membership.

Deliberately NOT unified with the separate, pre-existing domain-layer
KG data (a supply_chain.py warehouse ORGANIZATION entity, a
logistics.py rider PERSON entity, a finance.py payment-processor
ORGANIZATION entity) — those live in ad-hoc KnowledgeGraph() instances
the domain-layer tests build directly, a genuinely different
architectural layer from PlanetaryRuntime's own actor/geography/society
system; conflating the two is a separate, much larger undertaking this
ticket's chosen scope doesn't ask for.
"""

from __future__ import annotations

from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def _register_participant(marketplace, society_name, actor_name, actor_type):
    society_runtime = marketplace.create_society(name=society_name)
    profile = ActorProfile(identity=ActorIdentity(name=actor_name, actor_type=actor_type))
    actor = marketplace.register_actor(profile, society_id=society_runtime.society.society_id)
    return society_runtime, actor


def _assert_actor_is_coordinated(marketplace, society_runtime, actor):
    society_id = society_runtime.society.society_id

    # Actor created, and genuinely retrievable from the society it
    # registered into — not just a bare ActorRuntimeState nobody
    # actually holds a reference to.
    assert actor is not None
    assert society_runtime.get_actor(actor.actor_id) is not None

    # Membership registry agrees this actor belongs to this society.
    assert society_id in marketplace.membership_registry.societies_for_actor(actor.actor_id)

    # Actor Registration Invariant: the society is hosted at a real Space.
    assert marketplace.geo_registry.spaces_for_society(society_id) != ()
    marketplace.geo_registry.validate_society_has_space(society_id)  # must not raise

    # Presence initialized — the actor has a real, open current Space
    # from the moment registration completes.
    presence = marketplace.presence.current(actor.actor_id)
    assert presence is not None
    assert presence.is_open()

    # Effective membership immediately computable — no further setup
    # required before this actor can act.
    assert society_id in marketplace.effective_societies(actor.actor_id)


def test_mb3052_marketplace_society_coordinates_a_customer():
    marketplace = PlanetaryRuntime()
    society_runtime, alice = _register_participant(marketplace, "Marketplace Society", "Alice", ActorType.HUMAN)

    _assert_actor_is_coordinated(marketplace, society_runtime, alice)


def test_mb3052_merchant_society_coordinates_a_merchant():
    marketplace = PlanetaryRuntime()
    society_runtime, bob = _register_participant(
        marketplace,
        "Merchant Society",
        "Bob's Store",
        ActorType.ENTERPRISE,
    )

    _assert_actor_is_coordinated(marketplace, society_runtime, bob)


def test_mb3052_warehouse_society_coordinates_a_warehouse():
    marketplace = PlanetaryRuntime()
    society_runtime, warehouse = _register_participant(
        marketplace,
        "Warehouse Society",
        "Central Warehouse",
        ActorType.DEVICE,
    )

    _assert_actor_is_coordinated(marketplace, society_runtime, warehouse)


def test_mb3052_logistics_society_coordinates_a_delivery_rider():
    marketplace = PlanetaryRuntime()
    society_runtime, rider = _register_participant(
        marketplace,
        "Logistics Society",
        "Rider Rae",
        ActorType.HUMAN,
    )

    _assert_actor_is_coordinated(marketplace, society_runtime, rider)


def test_mb3052_payment_society_coordinates_a_payment_processor():
    marketplace = PlanetaryRuntime()
    society_runtime, processor = _register_participant(
        marketplace,
        "Payment Society",
        "PaySecure Gateway",
        ActorType.DIGITAL_SERVICE,
    )

    _assert_actor_is_coordinated(marketplace, society_runtime, processor)


def test_mb3052_all_five_societies_coexist_without_cross_contamination():
    marketplace = PlanetaryRuntime()
    participants = [
        _register_participant(marketplace, "Marketplace Society", "Alice", ActorType.HUMAN),
        _register_participant(marketplace, "Merchant Society", "Bob's Store", ActorType.ENTERPRISE),
        _register_participant(marketplace, "Warehouse Society", "Central Warehouse", ActorType.DEVICE),
        _register_participant(marketplace, "Logistics Society", "Rider Rae", ActorType.HUMAN),
        _register_participant(
            marketplace,
            "Payment Society",
            "PaySecure Gateway",
            ActorType.DIGITAL_SERVICE,
        ),
    ]

    society_ids = {society_runtime.society.society_id for society_runtime, _ in participants}
    assert len(society_ids) == 5

    for society_runtime, actor in participants:
        _assert_actor_is_coordinated(marketplace, society_runtime, actor)
        other_ids = society_ids - {society_runtime.society.society_id}
        actor_societies = set(marketplace.membership_registry.societies_for_actor(actor.actor_id))
        assert not (actor_societies & other_ids), f"{actor.profile.identity.name} leaked into another society"
