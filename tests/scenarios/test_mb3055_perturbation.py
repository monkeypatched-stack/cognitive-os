"""MB-3055 Perturbation — warehouse fire scenario.

Warehouse fire.

Verify: movement, reassignment, context updates.

Like MB-3052/3053/3054, no new code was needed:
kernel/society/movement_perturbation.py::MovementPerturbationEngine
(Prompt 7's Context-Driven Perturbation Engine, from earlier in this
session) already relocates every actor in a triggered Space through the
SAME move_actor() write path voluntary movement uses — so
PresenceTimeline updates, MembershipGovernor grant/revoke, and
ContextStream publication all already happen "for free," with zero
perturbation-specific logic for any of it.

This file triggers a real fire on a scenario combining a PERMANENT
Warehouse Society member (Worker Wendy) and a TEMPORARY one (Driver
Rae, whose permanent home is Logistics Society — MB-3054's exact
setup) and verifies all three things the ticket names:

  - movement: both actors are genuinely relocated to a real evacuation
    destination, not just reported as moved.
  - reassignment: Rae's TEMPORARY Warehouse membership is revoked
    (evacuated away from the Space that granted it) while Wendy's
    PERMANENT membership is completely unaffected by physical movement.
  - context updates: real ContextEvents (WORLD_UPDATE from the move
    itself, TEMPORARY_MEMBERSHIP_REVOKED from Rae's exit) land in
    ContextStream, not just returned in the perturbation's own record.

Investigation note: kernel/timeline/store.py::TimelineStore (backing
every PresenceTimeline) is a genuine process-wide singleton — every
PlanetaryRuntime() in the same test process shares one presence
history. perturb() picks ONE random space among ALL currently-occupied
spaces process-wide, so leftover actors from earlier tests in the same
process make an unbiased random pick unreliable (verified: even 50
retries wasn't always enough once several other scenario files had
already run in-process). _trigger_fire_at() below biases only the
"which occupied space is on fire" pick to this scenario's
warehouse_space via random.choice, while the SECOND random.choice call
inside the SAME perturb() (picking the evacuation destination among
real sibling Spaces) is left completely untouched — real, unmodified
production randomness for everything except which building catches
fire. A test-isolation accommodation for pre-existing, intentional
singleton architecture (mirrors kernel/plan/goals/run_store.py::
RunStore's same "backend chosen once" scaffolding), not a product bug
fixed here.
"""

from __future__ import annotations

import random as _random
from unittest.mock import patch

from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.society.context_stream import ContextEventType
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def _build_warehouse_fire_scenario():
    marketplace = PlanetaryRuntime()
    warehouse_society = marketplace.create_society(name="Warehouse Society")
    logistics_society = marketplace.create_society(name="Logistics Society")
    warehouse_id = warehouse_society.society.society_id
    logistics_id = logistics_society.society.society_id

    default_space_id = marketplace.default_bootstrap_space_id
    building = marketplace.geo_registry.parent_of(default_space_id)
    warehouse_space = marketplace.geo_registry.create(
        GeographicEntityType.SPACE,
        "Warehouse Floor",
        parent_id=building.entity_id,
    )
    marketplace.host_society(warehouse_space.entity_id, warehouse_id)

    # Worker Wendy: PERMANENT Warehouse Society member, based there.
    wendy = marketplace.register_actor(
        ActorProfile(identity=ActorIdentity(name="Worker Wendy", actor_type=ActorType.HUMAN)),
        society_id=warehouse_id,
        home_space_id=warehouse_space.entity_id,
    )
    # Driver Rae: PERMANENT Logistics Society member, currently delivering
    # inside the warehouse (TEMPORARY Warehouse membership, MB-3054).
    rae = marketplace.register_actor(
        ActorProfile(identity=ActorIdentity(name="Driver Rae", actor_type=ActorType.HUMAN)),
        society_id=logistics_id,
        home_space_id=default_space_id,
    )
    marketplace.move_actor(rae.actor_id, warehouse_space.entity_id, activity="delivering")
    assert warehouse_id in marketplace.membership_governor.temporary_societies_for_actor(rae.actor_id)

    return (
        marketplace,
        wendy,
        rae,
        warehouse_id,
        logistics_id,
        warehouse_space.entity_id,
    )


def _trigger_fire_at(marketplace, warehouse_space_id) -> list[dict]:
    """Fires perturb(event_chance=1.0), biasing only WHICH occupied
    space is picked (to this scenario's warehouse, when it's among the
    candidates — always true here) while leaving every other random
    choice inside the same call (evacuation destination, cause) fully
    real."""
    real_choice = _random.choice

    def biased_choice(seq):
        candidates = list(seq)
        if warehouse_space_id in candidates:
            return warehouse_space_id
        return real_choice(candidates)

    with patch(
        "src.monkey_brain.kernel.society.movement_perturbation.random.choice",
        side_effect=biased_choice,
    ):
        perturbations = marketplace._movement_perturbation.perturb(event_chance=1.0)
    return [p for p in perturbations if p["from_space_id"] == warehouse_space_id]


def test_mb3055_fire_evacuates_every_actor_present():
    marketplace, wendy, rae, _warehouse_id, _logistics_id, warehouse_space_id = _build_warehouse_fire_scenario()

    hit = _trigger_fire_at(marketplace, warehouse_space_id)

    assert len(hit) == 2
    assert {p["actor_id"] for p in hit} == {wendy.actor_id, rae.actor_id}
    for p in hit:
        assert p["perturbation"] == "movement"
        assert p["to_space_id"] != warehouse_space_id


def test_mb3055_movement_actually_relocates_both_actors():
    marketplace, wendy, rae, _warehouse_id, _logistics_id, warehouse_space_id = _build_warehouse_fire_scenario()

    hit = _trigger_fire_at(marketplace, warehouse_space_id)
    destination_id = hit[0]["to_space_id"]

    assert marketplace.presence.current(wendy.actor_id).space_id == destination_id
    assert marketplace.presence.current(rae.actor_id).space_id == destination_id
    assert marketplace.presence.current(wendy.actor_id).space_id != warehouse_space_id


def test_mb3055_reassignment_revokes_temporary_but_not_permanent_membership():
    marketplace, wendy, rae, warehouse_id, logistics_id, warehouse_space_id = _build_warehouse_fire_scenario()

    _trigger_fire_at(marketplace, warehouse_space_id)

    # Rae's TEMPORARY membership, granted only by physical presence, is
    # revoked once evacuated away from the Space that granted it.
    assert warehouse_id not in marketplace.membership_governor.temporary_societies_for_actor(rae.actor_id)
    assert warehouse_id not in marketplace.effective_societies(rae.actor_id)
    assert logistics_id in marketplace.effective_societies(rae.actor_id)

    # Wendy's PERMANENT membership is completely unaffected by being
    # physically relocated.
    assert warehouse_id in marketplace.effective_societies(wendy.actor_id)


def test_mb3055_context_updates_are_published_for_the_evacuation():
    marketplace, _wendy, rae, _warehouse_id, _logistics_id, warehouse_space_id = _build_warehouse_fire_scenario()
    events_before = marketplace.context_stream.event_count

    _trigger_fire_at(marketplace, warehouse_space_id)

    events_after = marketplace.context_stream.event_count
    new_events = marketplace.context_stream.events(limit=events_after - events_before)

    assert events_after > events_before
    assert any(e.event_type is ContextEventType.WORLD_UPDATE for e in new_events)
    assert any(
        e.event_type is ContextEventType.TEMPORARY_MEMBERSHIP_REVOKED and e.actor_id == rae.actor_id for e in new_events
    )
