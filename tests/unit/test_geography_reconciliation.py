"""Tests for PlanetaryRuntime.reconcile_default_geography() — the explicit,
opt-in cleanup of the eager bootstrap "Default Planet -> ... -> Default
Space" chain __init__ creates before real geography (e.g. seed_world.py's
"Earth") exists, so the two don't end up as permanent duplicate sibling
hierarchies. Never triggered automatically — __init__'s own bootstrap
timing/trigger condition is untouched by this feature.
"""

from __future__ import annotations

from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def _ancestor_planet_name(pr, entity_id):
    entity = pr._geo_registry.get(entity_id)
    while entity is not None and entity.entity_type != GeographicEntityType.PLANET:
        entity = pr._geo_registry.parent_of(entity.entity_id)
    return entity.name if entity is not None else None


def _create_real_earth_chain(pr):
    """A real canonical hierarchy, the same shape seed_world.py builds."""
    earth = pr._geo_registry.create(GeographicEntityType.PLANET, "Earth")
    usa = pr._geo_registry.create(GeographicEntityType.COUNTRY, "USA", parent_id=earth.entity_id)
    california = pr._geo_registry.create(GeographicEntityType.STATE, "California", parent_id=usa.entity_id)
    santa_clara = pr._geo_registry.create(
        GeographicEntityType.COUNTY,
        "Santa Clara County",
        parent_id=california.entity_id,
    )
    sunnyvale = pr._geo_registry.create(GeographicEntityType.CITY, "Sunnyvale", parent_id=santa_clara.entity_id)
    return earth, sunnyvale


def test_reconcile_migrates_society_onto_real_canonical_root():
    pr = PlanetaryRuntime()
    # A fresh PlanetaryRuntime() eagerly bootstraps the synthetic Default
    # chain and hosts its own bootstrap Default Society at Default City.
    bootstrap_society_id = pr.society.society_id
    assert pr.entity_for_society(bootstrap_society_id) is not None
    assert pr._default_planet is not None

    earth, sunnyvale = _create_real_earth_chain(pr)

    # A second, real Society explicitly hosted in the synthetic chain too —
    # proving reconciliation migrates EVERY hosted Society, not just the
    # one bootstrap default.
    club = pr.create_society("Book Club", society_type="community")
    pr.host_society(pr._default_city.entity_id, club.society.society_id)

    result = pr.reconcile_default_geography()

    assert result.performed is True
    assert result.canonical_root_id == earth.entity_id
    assert set(result.migrated_society_ids) == {
        bootstrap_society_id,
        club.society.society_id,
    }

    bootstrap_entity = pr.entity_for_society(bootstrap_society_id)
    club_entity = pr.entity_for_society(club.society.society_id)
    assert bootstrap_entity is not None
    assert club_entity is not None
    assert _ancestor_planet_name(pr, bootstrap_entity.entity_id) == "Earth"
    assert _ancestor_planet_name(pr, club_entity.entity_id) == "Earth"


def test_reconcile_removes_default_country_and_state_nodes():
    pr = PlanetaryRuntime()
    _create_real_earth_chain(pr)

    result = pr.reconcile_default_geography()
    assert result.performed is True

    countries = pr._geo_registry.all(GeographicEntityType.COUNTRY)
    assert not any(c.name == "Default Country" for c in countries)
    assert result.deleted_entity_ids  # the synthetic chain was actually removed


def test_reconcile_removes_default_state_node():
    pr = PlanetaryRuntime()
    _create_real_earth_chain(pr)

    result = pr.reconcile_default_geography()
    assert result.performed is True

    states = pr._geo_registry.all(GeographicEntityType.STATE)
    assert not any(s.name == "Default State" for s in states)


def test_reconcile_is_noop_when_no_canonical_root_exists():
    pr = PlanetaryRuntime()
    default_planet_id = pr._default_planet.entity_id

    result = pr.reconcile_default_geography()

    assert result.performed is False
    assert "Earth" in result.reason
    # Nothing touched — the synthetic chain is exactly as bootstrap left it.
    assert pr._geo_registry.get(default_planet_id) is not None
