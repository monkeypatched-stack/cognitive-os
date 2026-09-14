"""Tests for membership-gated WorldEntity visibility (CCB-202 follow-up):
an entity with owner_society_id set is only observable by actors with an
active Membership in that society; an entity with no owner (the default)
stays globally visible to everyone, unchanged. A bare ObservationProvider
with no membership_lookup wired in behaves exactly as before this change.
"""

from __future__ import annotations

from src.monkey_brain.kernel.society.observation import ObservationProvider
from src.monkey_brain.kernel.society.world import SharedWorld, WorldEntity
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
)
from src.monkey_brain.kernel.geography.entity import GeographicEntityType


def test_unscoped_entity_visible_to_everyone_no_lookup_wired():
    world = SharedWorld()
    world.add_entity(WorldEntity(name="global_thing", attributes={"x": 1}))
    provider = ObservationProvider(world)  # no membership_lookup — old behavior
    obs = provider.observe("anyone")
    assert any(e.entity.name == "global_thing" for e in obs.entities)


def test_scoped_entity_invisible_without_membership_lookup_wired_still_gated_off():
    """A membership_lookup that always denies must hide a scoped entity."""
    world = SharedWorld()
    world.add_entity(WorldEntity(name="scoped_thing", attributes={"x": 1}, owner_society_id="soc-1"))
    provider = ObservationProvider(world, membership_lookup=lambda actor_id, sid: False)
    obs = provider.observe("outsider")
    assert not any(e.entity.name == "scoped_thing" for e in obs.entities)


def test_scoped_entity_visible_to_member():
    world = SharedWorld()
    world.add_entity(WorldEntity(name="scoped_thing", attributes={"x": 1}, owner_society_id="soc-1"))
    provider = ObservationProvider(world, membership_lookup=lambda actor_id, sid: actor_id == "member")
    obs = provider.observe("member")
    assert any(e.entity.name == "scoped_thing" for e in obs.entities)
    obs2 = provider.observe("stranger")
    assert not any(e.entity.name == "scoped_thing" for e in obs2.entities)


def test_unscoped_entity_still_visible_when_lookup_is_wired():
    """owner_society_id="" (the default) must never be gated, regardless
    of whether a membership_lookup is present."""
    world = SharedWorld()
    world.add_entity(WorldEntity(name="global_thing", attributes={"x": 1}))
    provider = ObservationProvider(world, membership_lookup=lambda actor_id, sid: False)
    obs = provider.observe("anyone")
    assert any(e.entity.name == "global_thing" for e in obs.entities)


def test_real_planetary_runtime_gates_by_active_membership():
    """End-to-end through PlanetaryRuntime._attach_society's real wiring:
    an actor who leaves (terminated membership) loses visibility of a
    society-scoped entity; an active member keeps it."""
    pr = PlanetaryRuntime()
    soc = pr.create_society("Household", society_type="household", always_active=True)
    # create_society() auto-hosts every new society at the same shared
    # bootstrap "Default City" register_actor()'s own default home space
    # is under (see add_society's docstring) — left as-is, the Member
    # below would ALSO become a TEMPORARY (presence-derived) participant
    # of "Household" merely by physically occupying that shared city,
    # independent of the explicit join_society() PERMANENT membership
    # this test terminates below. That extra, unintended temporary grant
    # would still make the pantry visible after termination, masking the
    # very thing this test checks. Rehost "Household" to its own city so
    # the Member's only tie to it is the explicit permanent membership.
    country = pr.create_country("Observation Scoping Country")
    city = pr.create_city("Observation Scoping City", country.entity_id)
    pr.assign_society_to_city(soc.society.society_id, city.entity_id)
    member = pr.register_actor(ActorProfile(identity=ActorIdentity(name="Member", actor_type=ActorType.HUMAN)))
    pr.join_society(member.actor_id, soc.society.society_id, role="member")

    pantry = WorldEntity(name="pantry", attributes={"milk": 1}, owner_society_id=soc.society.society_id)
    pr.add_world_entity(pantry)

    obs = soc.get_observation(member.actor_id)
    assert any(e.entity.name == "pantry" for e in obs.entities)

    # register_actor() also creates a "home" membership in a different,
    # default society — find the one for THIS household specifically.
    membership = next(
        m
        for m in pr.membership_registry.memberships_for_actor(member.actor_id)
        if m.society_id == soc.society.society_id
    )
    pr.membership_registry.set_status(membership.membership_id, "terminated")

    obs_after = soc.get_observation(member.actor_id)
    assert not any(e.entity.name == "pantry" for e in obs_after.entities)
