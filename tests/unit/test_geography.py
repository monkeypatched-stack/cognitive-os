"""Tests for the Physical Geography Hierarchy & Society Decoupling refactor.

Covers the data invariants GeographicRegistry enforces (tier adjacency,
single-parent containment, single-host society hosting) and the tick
cascade through GeographicEntityRuntime — the "ticks propagate from planet
to actors" requirement, now through the FULL 8-tier hierarchy rather than
the earlier 2-tier Country->City model.
"""
from __future__ import annotations

import asyncio
import pytest

from src.monkey_brain.kernel.geography.entity import (
    GeographicEntityType, BuildingType, SpaceType, PARENT_TIER, ROOT_ELIGIBLE,
    Planet, Country, State, County, City, Street, Building, Space,
)
from src.monkey_brain.kernel.geography.registry import GeographicRegistry
from src.monkey_brain.kernel.geography.runtime import GeographicEntityRuntime


def _build_full_chain(registry: GeographicRegistry):
    """Planet -> Country -> State -> County -> City -> Street -> Building
    -> Space, via registry.create() (the sole constructor path)."""
    planet = registry.create(GeographicEntityType.PLANET, "Earth")
    country = registry.create(GeographicEntityType.COUNTRY, "Country", parent_id=planet.entity_id)
    state = registry.create(GeographicEntityType.STATE, "State", parent_id=country.entity_id)
    county = registry.create(GeographicEntityType.COUNTY, "County", parent_id=state.entity_id)
    city = registry.create(GeographicEntityType.CITY, "City", parent_id=county.entity_id)
    street = registry.create(GeographicEntityType.STREET, "Street", parent_id=city.entity_id)
    building = registry.create(
        GeographicEntityType.BUILDING, "Building", parent_id=street.entity_id,
        building_type=BuildingType.MIXED_USE,
    )
    space = registry.create(
        GeographicEntityType.SPACE, "Space", parent_id=building.entity_id,
        space_type=SpaceType.OFFICE,
    )
    return planet, country, state, county, city, street, building, space


class _FakeActor:
    """Bare stand-in with just the attribute GeographicEntityRuntime.tick()
    reads off each active actor (a.actor_id, to dedupe cross-tier ticks via
    exclude_actor_ids — see runtime.py)."""

    def __init__(self, actor_id: str) -> None:
        self.actor_id = actor_id


class _FakeSocietyRuntime:
    """Minimal stand-in for SocietyRuntime — GeographicEntityRuntime.tick()
    only needs is_active, active_actors(), and an async tick()."""

    def __init__(self, society_id: str, actors_ticked: int = 1) -> None:
        self.society_id = society_id
        self.is_active = True
        self._actors_ticked = actors_ticked
        self.tick_calls = 0

    def active_actors(self):
        return [_FakeActor(f"{self.society_id}-actor-{i}") for i in range(self._actors_ticked)]

    async def tick(self, *, target_actor_id=None, prompt_request=None, exclude_actor_ids=None):
        self.tick_calls += 1

        class _Result:
            actors_ticked = self._actors_ticked
            interactions_routed = 0

        return _Result()


# ── Tier construction & adjacency ────────────────────────────────────────

def test_full_chain_construction():
    registry = GeographicRegistry()
    planet, country, state, county, city, street, building, space = _build_full_chain(registry)
    assert space.parent_id == building.entity_id
    assert building.parent_id == street.entity_id
    assert street.parent_id == city.entity_id
    assert city.parent_id == county.entity_id
    assert county.parent_id == state.entity_id
    assert state.parent_id == country.entity_id
    assert country.parent_id == planet.entity_id
    assert planet.parent_id is None


def test_building_rejected_directly_under_country():
    """Buildings must be parented under Street, not Country — tier
    adjacency (PARENT_TIER) makes this structurally impossible, the
    mechanism that rules out circular containment."""
    registry = GeographicRegistry()
    planet = registry.create(GeographicEntityType.PLANET, "Earth")
    country = registry.create(GeographicEntityType.COUNTRY, "Country", parent_id=planet.entity_id)

    result = registry.create(GeographicEntityType.BUILDING, "BadBuilding", parent_id=country.entity_id)
    assert result is None
    # No orphaned entity left behind in the registry.
    assert len(registry.all(GeographicEntityType.BUILDING)) == 0


def test_add_child_rejects_invalid_tier_pairing_directly():
    registry = GeographicRegistry()
    country = registry.register(Country(name="C"))
    building = registry.register(Building(name="B"))
    result = registry.add_child(country.entity_id, building.entity_id)
    assert result is None
    assert registry.parent_of(building.entity_id) is None


def test_space_has_exactly_one_parent_building():
    registry = GeographicRegistry()
    _, _, _, _, _, street, building, space = _build_full_chain(registry)
    other_building = registry.create(GeographicEntityType.BUILDING, "Other", parent_id=street.entity_id)

    # Reassign space to a different building — single parent enforced.
    registry.add_child(other_building.entity_id, space.entity_id)
    space_now = registry.get(space.entity_id)
    assert space_now.parent_id == other_building.entity_id

    old_building = registry.get(building.entity_id)
    new_building = registry.get(other_building.entity_id)
    assert space.entity_id not in old_building.child_ids
    assert space.entity_id in new_building.child_ids


def test_no_circular_containment_possible_by_construction():
    """PARENT_TIER is a DAG (Universe/Region/Floor/Coordinate extend the
    original 8-tier hierarchy, so some tiers — COUNTRY, SPACE — now accept
    more than one valid parent tier, e.g. COUNTRY under PLANET or REGION):
    walking every "required parent tier" branch upward from any tier must
    terminate at a root-eligible tier (ROOT_ELIGIBLE: UNIVERSE or PLANET)
    without ever revisiting a tier on that branch — that termination is
    what rules out a cycle."""
    for tier in GeographicEntityType:
        def _walk(current: GeographicEntityType, visited: frozenset[GeographicEntityType]) -> None:
            assert current not in visited, f"cycle detected walking up from {tier}"
            if current not in PARENT_TIER:
                assert current in ROOT_ELIGIBLE, f"{tier} does not terminate at a root tier"
                return
            for parent in PARENT_TIER[current]:
                _walk(parent, visited | {current})

        _walk(tier, frozenset())


# ── Society hosting: any tier, single-host ───────────────────────────────

def test_society_hosted_at_any_tier():
    registry = GeographicRegistry()
    planet, country, state, county, city, street, building, space = _build_full_chain(registry)
    hosted_at_building = registry.host_society(building.entity_id, "soc-1")
    assert hosted_at_building.hosts_society("soc-1")
    assert registry.entity_for_society("soc-1").entity_id == building.entity_id


def test_hosting_society_moves_not_duplicates():
    registry = GeographicRegistry()
    planet, country, state, county, city, street, building, space = _build_full_chain(registry)
    registry.host_society(building.entity_id, "soc-1")
    registry.host_society(space.entity_id, "soc-1")

    building_now = registry.get(building.entity_id)
    space_now = registry.get(space.entity_id)
    assert not building_now.hosts_society("soc-1")
    assert space_now.hosts_society("soc-1")
    assert registry.entity_for_society("soc-1").entity_id == space.entity_id


def test_ancestor_of_type_walks_full_depth():
    registry = GeographicRegistry()
    planet, country, state, county, city, street, building, space = _build_full_chain(registry)
    assert registry.ancestor_of_type(space.entity_id, GeographicEntityType.COUNTRY).entity_id == country.entity_id
    assert registry.ancestor_of_type(space.entity_id, GeographicEntityType.PLANET).entity_id == planet.entity_id
    assert registry.ancestor_of_type(space.entity_id, GeographicEntityType.SPACE).entity_id == space.entity_id


# ── Tick cascade: planet -> actors, full 8-tier depth ────────────────────

def test_tick_cascades_full_depth_to_hosted_society():
    registry = GeographicRegistry()
    planet, country, state, county, city, street, building, space = _build_full_chain(registry)
    registry.host_society(space.entity_id, "soc-1")
    fake_society = _FakeSocietyRuntime("soc-1", actors_ticked=3)

    async def run():
        runtime = GeographicEntityRuntime(registry, planet.entity_id, {"soc-1": fake_society}.get)
        return await runtime.tick()

    result = asyncio.run(run())
    assert result.actors_ticked_total == 3
    assert fake_society.tick_calls == 1
    # children_ticked lists only direct children — for the planet root,
    # that's the country tier; the cascade below it is proven by the
    # aggregated actors_ticked_total reaching all the way from the Space.
    assert result.children_ticked == (country.entity_id,)


def test_tick_skips_inactive_society():
    registry = GeographicRegistry()
    planet, *_rest, space = _build_full_chain(registry)
    registry.host_society(space.entity_id, "soc-1")
    fake_society = _FakeSocietyRuntime("soc-1", actors_ticked=5)
    fake_society.is_active = False

    async def run():
        runtime = GeographicEntityRuntime(registry, planet.entity_id, {"soc-1": fake_society}.get)
        return await runtime.tick()

    result = asyncio.run(run())
    assert result.actors_ticked_total == 0
    assert fake_society.tick_calls == 0


def test_tick_skips_society_with_no_active_actors():
    registry = GeographicRegistry()
    planet, *_rest, space = _build_full_chain(registry)
    registry.host_society(space.entity_id, "soc-1")
    fake_society = _FakeSocietyRuntime("soc-1", actors_ticked=0)

    async def run():
        runtime = GeographicEntityRuntime(registry, planet.entity_id, {"soc-1": fake_society}.get)
        return await runtime.tick()

    result = asyncio.run(run())
    assert result.actors_ticked_total == 0
    assert fake_society.tick_calls == 0


def test_multiple_societies_hosted_at_different_tiers_all_reached():
    registry = GeographicRegistry()
    planet, country, state, county, city, street, building, space = _build_full_chain(registry)
    registry.host_society(country.entity_id, "soc-country")
    registry.host_society(building.entity_id, "soc-building")
    societies = {
        "soc-country": _FakeSocietyRuntime("soc-country", actors_ticked=2),
        "soc-building": _FakeSocietyRuntime("soc-building", actors_ticked=4),
    }

    async def run():
        runtime = GeographicEntityRuntime(registry, planet.entity_id, societies.get)
        return await runtime.tick()

    result = asyncio.run(run())
    assert result.actors_ticked_total == 6
    assert societies["soc-country"].tick_calls == 1
    assert societies["soc-building"].tick_calls == 1


# ── Registry.create() — the sole constructor entry point ─────────────────

def test_create_rejects_unknown_entity_type_gracefully():
    registry = GeographicRegistry()

    class _NotARealType:
        pass

    assert registry.create(_NotARealType(), "X") is None


def test_create_requires_parent_for_non_planet_tiers():
    registry = GeographicRegistry()
    assert registry.create(GeographicEntityType.COUNTRY, "NoParent") is None


def test_create_planet_needs_no_parent():
    registry = GeographicRegistry()
    planet = registry.create(GeographicEntityType.PLANET, "Earth")
    assert planet is not None
    assert planet.entity_type == GeographicEntityType.PLANET
    assert planet.parent_id is None


class _FakePresence:
    """Minimal PresenceLookup stand-in — GeographicEntityRuntime.tick()
    only calls .occupants(entity_id) (kernel/geography/runtime.py:420)."""

    def __init__(self, entity_occupants: dict[str, list[str]]) -> None:
        self._entity_occupants = entity_occupants

    def occupants(self, entity_id: str) -> list[str]:
        return self._entity_occupants.get(entity_id, [])


class TestOccupantTicksRunConcurrently:
    """Society architecture review, Phase 4: docs/adr/016-performance-gate9.md
    and docs/adr/019-runtime-performance-audit.md found (and measured live,
    30+s for 10 actors) that GeographicEntityRuntime.tick() awaited each
    present actor's LLM-backed cognitive tick SERIALLY. The current source
    (runtime.py:419-425) already awaits every occupant via asyncio.gather(),
    with a code comment justifying why this is safe (each occupant only
    writes to its own keyed slot). That fix existed with NO test covering
    it at all -- grep for "occupants"/"presence" in this file before this
    test found zero matches. This closes that gap: proves N occupants'
    ticks actually overlap in wall-clock time, not merely that the code
    calls asyncio.gather (which could still be trivially true even if
    concurrency were accidentally serialized by a bug elsewhere)."""

    def test_n_occupant_ticks_complete_in_roughly_one_ticks_time_not_n_times(self):
        import time

        registry = GeographicRegistry()
        planet = registry.create(GeographicEntityType.PLANET, "Earth")

        n = 8
        delay_seconds = 0.05
        occupant_ids = [f"actor-{i}" for i in range(n)]
        presence = _FakePresence({planet.entity_id: occupant_ids})

        async def slow_actor_ticker(actor_id: str) -> bool:
            await asyncio.sleep(delay_seconds)
            return True

        async def run():
            runtime = GeographicEntityRuntime(
                registry, planet.entity_id, {}.get,
                presence=presence, actor_ticker=slow_actor_ticker,
            )
            start = time.perf_counter()
            result = await runtime.tick()
            elapsed = time.perf_counter() - start
            return result, elapsed

        result, elapsed = asyncio.run(run())

        assert result.actors_ticked_total == n
        # Serial execution would take n * delay_seconds (0.4s for n=8);
        # concurrent execution takes roughly one delay_seconds regardless
        # of n. A generous multiple of one delay (not n delays) proves
        # real overlap without being sensitive to test-runner scheduling
        # noise.
        assert elapsed < delay_seconds * (n / 2), (
            f"occupant ticks took {elapsed:.3f}s for n={n} actors at "
            f"{delay_seconds}s each -- expected them to overlap (~{delay_seconds:.3f}s), "
            f"not run serially (~{n * delay_seconds:.3f}s)"
        )

    def test_one_occupants_tick_failure_does_not_prevent_others_from_completing(self):
        """The comment justifying gather() over the serial loop claims each
        occupant only touches its own keyed slot -- proven here: one
        occupant's ticker raising must not stop the others from being
        counted."""
        registry = GeographicRegistry()
        planet = registry.create(GeographicEntityType.PLANET, "Earth")

        occupant_ids = ["good-1", "bad", "good-2"]
        presence = _FakePresence({planet.entity_id: occupant_ids})

        async def flaky_actor_ticker(actor_id: str) -> bool:
            if actor_id == "bad":
                raise RuntimeError("simulated actor tick failure")
            return True

        async def run():
            runtime = GeographicEntityRuntime(
                registry, planet.entity_id, {}.get,
                presence=presence, actor_ticker=flaky_actor_ticker,
            )
            return await runtime.tick()

        result = asyncio.run(run())
        assert result.actors_ticked_total == 2
