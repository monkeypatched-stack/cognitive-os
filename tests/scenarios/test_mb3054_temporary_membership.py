"""MB-3054 Temporary Membership — driver enters warehouse scenario.

Driver enters warehouse. Temporary membership granted. Leaves.
Membership revoked.

Like MB-3052/3053, no new code was needed: kernel/society/membership.py::
MembershipGovernor (Prompt 3's Governance and Membership Model, from
earlier in this session) already implements exactly this — subscribing
to PresenceTimeline movement, granting TEMPORARY membership in every
Society hosted at a Space an actor enters, revoking it the moment they
leave, while never touching PERMANENT membership. This file verifies
the concrete business scenario the ticket names: a driver whose
PERMANENT home is Logistics Society enters a Space hosted by Warehouse
Society, gets a real temporary membership there (and becomes a genuine
coordination participant in the Warehouse SocietyRuntime — the
Coordination Boundary refactor), then leaves and has it cleanly
revoked, with Logistics Society membership intact throughout.

Registration pins home_space_id explicitly rather than leaving it to
register_actor()'s default space-selection — a freshly created
"Warehouse Floor" Space sharing the same city/building subtree every
auto-created Society is hosted at by default becomes a valid, ambiguous
home-space candidate otherwise, which would make the driver start out
already warehouse-adjacent instead of cleanly outside it.
"""

from __future__ import annotations

from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def _build_marketplace():
    marketplace = PlanetaryRuntime()
    logistics_society = marketplace.create_society(name="Logistics Society")
    warehouse_society = marketplace.create_society(name="Warehouse Society")
    logistics_id = logistics_society.society.society_id
    warehouse_id = warehouse_society.society.society_id

    default_space_id = marketplace.default_bootstrap_space_id
    building = marketplace.geo_registry.parent_of(default_space_id)
    warehouse_space = marketplace.geo_registry.create(
        GeographicEntityType.SPACE,
        "Warehouse Floor",
        parent_id=building.entity_id,
    )
    marketplace.host_society(warehouse_space.entity_id, warehouse_id)

    driver = marketplace.register_actor(
        ActorProfile(identity=ActorIdentity(name="Rider Rae", actor_type=ActorType.HUMAN)),
        society_id=logistics_id,
        home_space_id=default_space_id,
    )
    return (
        marketplace,
        driver,
        logistics_id,
        warehouse_id,
        default_space_id,
        warehouse_space.entity_id,
    )


def test_mb3054_driver_has_no_warehouse_membership_before_entering():
    (
        marketplace,
        driver,
        logistics_id,
        warehouse_id,
        _default_space,
        _warehouse_space,
    ) = _build_marketplace()

    effective = marketplace.effective_societies(driver.actor_id)

    assert logistics_id in effective
    assert warehouse_id not in effective


def test_mb3054_entering_the_warehouse_grants_temporary_membership():
    marketplace, driver, logistics_id, warehouse_id, _default_space, warehouse_space = _build_marketplace()

    marketplace.move_actor(driver.actor_id, warehouse_space, activity="delivering")

    temp = marketplace.membership_governor.temporary_societies_for_actor(driver.actor_id)
    assert warehouse_id in temp
    assert warehouse_id in marketplace.effective_societies(driver.actor_id)
    assert logistics_id in marketplace.effective_societies(driver.actor_id)


def test_mb3054_driver_becomes_a_real_coordination_participant_while_present():
    (
        marketplace,
        driver,
        _logistics_id,
        warehouse_id,
        _default_space,
        warehouse_space,
    ) = _build_marketplace()

    marketplace.move_actor(driver.actor_id, warehouse_space, activity="delivering")

    warehouse_runtime = marketplace.get_society_runtime(warehouse_id)
    assert warehouse_runtime.get_actor(driver.actor_id) is not None


def test_mb3054_permanent_membership_registry_is_never_touched():
    marketplace, driver, logistics_id, warehouse_id, _default_space, warehouse_space = _build_marketplace()

    marketplace.move_actor(driver.actor_id, warehouse_space, activity="delivering")

    assert logistics_id in marketplace.membership_registry.societies_for_actor(driver.actor_id)
    assert warehouse_id not in marketplace.membership_registry.societies_for_actor(driver.actor_id)


def test_mb3054_leaving_revokes_temporary_membership():
    marketplace, driver, logistics_id, warehouse_id, default_space, warehouse_space = _build_marketplace()
    marketplace.move_actor(driver.actor_id, warehouse_space, activity="delivering")

    marketplace.move_actor(driver.actor_id, default_space, activity="departing")

    temp_after = marketplace.membership_governor.temporary_societies_for_actor(driver.actor_id)
    assert warehouse_id not in temp_after
    assert warehouse_id not in marketplace.effective_societies(driver.actor_id)
    assert logistics_id in marketplace.effective_societies(driver.actor_id)


def test_mb3054_leaving_removes_the_coordination_participant():
    marketplace, driver, _logistics_id, warehouse_id, default_space, warehouse_space = _build_marketplace()
    marketplace.move_actor(driver.actor_id, warehouse_space, activity="delivering")
    warehouse_runtime = marketplace.get_society_runtime(warehouse_id)

    marketplace.move_actor(driver.actor_id, default_space, activity="departing")

    assert warehouse_runtime.get_actor(driver.actor_id) is None
