"""MB-3058 Country Tick — tick country scenario.

Tick country.

Verify: recursive traversal.

Like MB-3057, no new code was needed:
kernel/geography/runtime.py::GeographicEntityRuntime already recurses
through every one of the 8 tiers (Planet -> Country -> State -> County
-> City -> Street -> Building -> Space), ticking each entity plus every
Society hosted there, then every child recursively via a fresh
GeographicEntityRuntime — one class, all 8 tiers (Prompt 4).

This file builds a FULL, dedicated Country -> State -> County -> City
-> Street -> Building -> Space chain (distinct from the marketplace's
own default bootstrap chain), places one Society and one actor at the
very BOTTOM (Space) tier, then ticks starting from the COUNTRY — six
tiers above the actor — and verifies the recursion genuinely walks the
entire depth: every one of the 7 created entities is counted in
entities_ticked_total, the deeply-nested Space is observed, and the
actor at the bottom actually gets ticked (its cognition state advances)
despite the tick call never directly referencing anything below the
Country.
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


def _build_deep_chain():
    marketplace = PlanetaryRuntime()

    country = marketplace.geo_registry.create(
        GeographicEntityType.COUNTRY,
        "Testland",
        parent_id=marketplace._default_planet.entity_id,
    )
    state = marketplace.geo_registry.create(GeographicEntityType.STATE, "Test State", parent_id=country.entity_id)
    county = marketplace.geo_registry.create(GeographicEntityType.COUNTY, "Test County", parent_id=state.entity_id)
    city = marketplace.geo_registry.create(GeographicEntityType.CITY, "Test City", parent_id=county.entity_id)
    street = marketplace.geo_registry.create(GeographicEntityType.STREET, "Test Street", parent_id=city.entity_id)
    building = marketplace.geo_registry.create(
        GeographicEntityType.BUILDING, "Test Building", parent_id=street.entity_id
    )
    space = marketplace.geo_registry.create(GeographicEntityType.SPACE, "Test Space", parent_id=building.entity_id)

    deep_society = marketplace.create_society(name="Deep Society", always_active=True)
    marketplace.host_society(space.entity_id, deep_society.society.society_id)

    actor = marketplace.register_actor(
        ActorProfile(identity=ActorIdentity(name="Deep Dan", actor_type=ActorType.HUMAN)),
        society_id=deep_society.society.society_id,
        home_space_id=space.entity_id,
    )

    country_runtime = GeographicEntityRuntime(
        marketplace.geo_registry,
        country.entity_id,
        marketplace._societies.get,
        presence=marketplace.presence,
        actor_ticker=marketplace._tick_present_actor,
        membership_reconciler=marketplace.membership_governor.reconcile,
        temporary_membership_lookup=marketplace._temporary_membership_lookup,
        effective_membership_lookup=marketplace._effective_membership_lookup,
    )
    return marketplace, deep_society, actor, space.entity_id, country_runtime


@pytest.mark.asyncio
async def test_mb3058_ticking_the_country_walks_every_one_of_the_seven_tiers():
    _marketplace, _deep_society, _actor, _space_id, country_runtime = _build_deep_chain()

    result = await country_runtime.tick()

    # Country, State, County, City, Street, Building, Space — all 7
    # entities in this dedicated chain, none skipped.
    assert result.entities_ticked_total == 7


@pytest.mark.asyncio
async def test_mb3058_the_deeply_nested_space_is_observed():
    _marketplace, _deep_society, _actor, space_id, country_runtime = _build_deep_chain()

    result = await country_runtime.tick()

    assert space_id in result.observed_spaces


@pytest.mark.asyncio
async def test_mb3058_the_actor_six_tiers_down_actually_gets_ticked():
    _marketplace, deep_society, actor, _space_id, country_runtime = _build_deep_chain()
    before = deep_society.get_actor(actor.actor_id).cycle_count

    result = await country_runtime.tick()

    assert actor.actor_id in result.observed_actors
    assert result.actors_ticked_total >= 1
    after = deep_society.get_actor(actor.actor_id).cycle_count
    assert after > before
