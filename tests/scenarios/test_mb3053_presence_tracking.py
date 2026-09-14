"""MB-3053 Presence Tracking — verify where_is/history/who_is_in.

Verify: where_is(actor), history(actor), who_is_in(space).

Like MB-3010/3015/3028/3032/3046/3047, no new code was needed:
kernel/timeline/presence.py::PresenceTimeline already implements all
three under slightly different names, exposed on every PlanetaryRuntime
via its `presence` property (already in real use elsewhere — e.g.
integration.py's own who-is-here helper calls
self.presence.occupants(space_id) internally):

  - where_is(actor)  -> PresenceTimeline.current(actor_id)
  - history(actor)   -> PresenceTimeline.history(actor_id, since, until)
  - who_is_in(space) -> PresenceTimeline.occupants(space_id, timestamp)

Verified end to end against a real PlanetaryRuntime: registering two
actors, moving one to a second real Space, and checking that current(),
history(), and occupants() (including occupants() at a PAST timestamp,
proving its time-travel semantics) all agree with what actually
happened.
"""

from __future__ import annotations

import time

from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def _seed_two_actors_and_a_second_space():
    marketplace = PlanetaryRuntime()
    alice = marketplace.register_actor(ActorProfile(identity=ActorIdentity(name="Alice", actor_type=ActorType.HUMAN)))
    bob = marketplace.register_actor(ActorProfile(identity=ActorIdentity(name="Bob", actor_type=ActorType.HUMAN)))

    default_space_id = marketplace.default_bootstrap_space_id
    building = marketplace.geo_registry.parent_of(default_space_id)
    second_space = marketplace.geo_registry.create(
        GeographicEntityType.SPACE,
        "Warehouse Floor",
        parent_id=building.entity_id,
    )
    return marketplace, alice, bob, default_space_id, second_space.entity_id


def test_mb3053_where_is_reflects_current_location():
    marketplace, alice, _bob, default_space_id, second_space_id = _seed_two_actors_and_a_second_space()

    assert marketplace.presence.current(alice.actor_id).space_id == default_space_id

    marketplace.move_actor(alice.actor_id, second_space_id, activity="shopping")

    assert marketplace.presence.current(alice.actor_id).space_id == second_space_id


def test_mb3053_history_shows_the_closed_then_open_sequence():
    marketplace, alice, _bob, default_space_id, second_space_id = _seed_two_actors_and_a_second_space()

    marketplace.move_actor(alice.actor_id, second_space_id, activity="shopping")

    history = marketplace.presence.history(alice.actor_id)

    assert len(history) == 2
    assert history[0].space_id == default_space_id
    assert history[0].is_open() is False
    assert history[1].space_id == second_space_id
    assert history[1].is_open() is True


def test_mb3053_who_is_in_reports_current_occupants():
    marketplace, alice, bob, default_space_id, second_space_id = _seed_two_actors_and_a_second_space()

    marketplace.move_actor(alice.actor_id, second_space_id, activity="shopping")

    default_occupants = marketplace.presence.occupants(default_space_id)
    second_occupants = marketplace.presence.occupants(second_space_id)

    assert bob.actor_id in default_occupants
    assert alice.actor_id not in default_occupants
    assert alice.actor_id in second_occupants


def test_mb3053_who_is_in_at_a_past_timestamp_reflects_history_not_now():
    marketplace, alice, _bob, default_space_id, second_space_id = _seed_two_actors_and_a_second_space()
    before_move = time.time()

    marketplace.move_actor(alice.actor_id, second_space_id, activity="shopping")

    past_occupants = marketplace.presence.occupants(default_space_id, timestamp=before_move - 0.001)
    now_occupants = marketplace.presence.occupants(default_space_id)

    assert alice.actor_id in past_occupants
    assert alice.actor_id not in now_occupants
