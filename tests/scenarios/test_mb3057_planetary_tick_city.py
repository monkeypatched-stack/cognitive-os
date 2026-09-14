"""MB-3057 Planetary Tick — tick city scenario.

Tick city.

Verify: entire commerce ecosystem updates correctly.

Like MB-3052/3053/3054/3055/3056, no new code was needed:
kernel/geography/runtime.py::GeographicEntityRuntime (Prompt 4's
Recursive Geographic Tick, from earlier in this session) already ticks
one geographic entity plus every Society hosted there recursively — it
can be pointed at ANY tier, including a City directly, using the exact
same wiring PlanetaryRuntime's own cycle() uses internally.

This file registers MB-3052's full 5-society commerce ecosystem
(Marketplace, Merchant, Warehouse, Logistics, Payment Societies, one
representative actor each) — all hosted under the marketplace's Default
City via the default bootstrap Space — then ticks ONLY that City
directly (not the whole Planet) and verifies the entire ecosystem
genuinely updates: every one of the 5 participants is observed, ticked,
and has its cognition state (cycle_count) actually advance, from one
single City-scoped tick.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.geography.runtime import GeographicEntityRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

ECOSYSTEM = [
    ("Marketplace Society", "Alice", ActorType.HUMAN),
    ("Merchant Society", "Bob's Store", ActorType.ENTERPRISE),
    ("Warehouse Society", "Central Warehouse", ActorType.DEVICE),
    ("Logistics Society", "Rider Rae", ActorType.HUMAN),
    ("Payment Society", "PaySecure Gateway", ActorType.DIGITAL_SERVICE),
]


def _city_scoped_runtime(marketplace: PlanetaryRuntime, city_entity_id: str) -> GeographicEntityRuntime:
    return GeographicEntityRuntime(
        marketplace.geo_registry,
        city_entity_id,
        marketplace._societies.get,
        presence=marketplace.presence,
        actor_ticker=marketplace._tick_present_actor,
        membership_reconciler=marketplace.membership_governor.reconcile,
        temporary_membership_lookup=marketplace._temporary_membership_lookup,
        effective_membership_lookup=marketplace._effective_membership_lookup,
    )


def _build_commerce_ecosystem():
    marketplace = PlanetaryRuntime()
    participants = []
    for society_name, actor_name, actor_type in ECOSYSTEM:
        society_runtime = marketplace.create_society(name=society_name, always_active=True)
        actor = marketplace.register_actor(
            ActorProfile(identity=ActorIdentity(name=actor_name, actor_type=actor_type)),
            society_id=society_runtime.society.society_id,
        )
        participants.append((society_runtime, actor))

    city = marketplace.geo_registry.ancestor_of_type(
        marketplace.default_bootstrap_space_id,
        GeographicEntityType.CITY,
    )
    return marketplace, participants, city.entity_id


@pytest.mark.asyncio
async def test_mb3057_ticking_the_city_observes_every_ecosystem_participant():
    marketplace, participants, city_id = _build_commerce_ecosystem()

    result = await _city_scoped_runtime(marketplace, city_id).tick()

    all_actor_ids = {actor.actor_id for _sr, actor in participants}
    assert all_actor_ids <= set(result.observed_actors)
    assert all_actor_ids <= set(result.active_actors)
    assert result.actors_ticked_total >= len(participants)


@pytest.mark.asyncio
async def test_mb3057_ticking_the_city_advances_every_participants_cognition():
    marketplace, participants, city_id = _build_commerce_ecosystem()
    before = {actor.actor_id: actor.cycle_count for _sr, actor in participants}

    await _city_scoped_runtime(marketplace, city_id).tick()

    for society_runtime, actor in participants:
        after = society_runtime.get_actor(actor.actor_id).cycle_count
        assert after > before[actor.actor_id], f"{actor.profile.identity.name} did not advance"


@pytest.mark.asyncio
async def test_mb3057_ticking_the_city_reaches_every_hosted_society():
    marketplace, participants, city_id = _build_commerce_ecosystem()

    result = await _city_scoped_runtime(marketplace, city_id).tick()

    society_ids = {sr.society.society_id for sr, _actor in participants}
    assert society_ids <= set(result.observed_societies)
