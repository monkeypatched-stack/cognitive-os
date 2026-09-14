"""Governance, Membership and Registration Model — coverage for the
refactor that cleanly separates physical presence (PresenceTimeline/Space),
governance (Society), permanent membership (SocietyMembershipRegistry),
and temporary membership (MembershipGovernor), and guarantees no public
API can leave the world in a state where a Society has no associated
Space.

Covers: permanent memberships, temporary memberships, registration (the
Actor Registration Invariant), movement, membership transitions,
validation failures, and effective membership computation.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.society.context_stream import ContextEventType
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def _register(pr: PlanetaryRuntime, name: str = "Alice", **kwargs):
    profile = ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN))
    return pr.register_actor(profile, **kwargs)


def _build_chain(pr: PlanetaryRuntime, *space_names: str) -> list:
    """Planet -> Country -> State -> County -> City -> Street -> Building
    -> one or more sibling Spaces, all under the same Building."""
    planet = pr.create_geographic_entity(GeographicEntityType.PLANET, "P", None)
    country = pr.create_geographic_entity(GeographicEntityType.COUNTRY, "Co", planet.entity_id)
    state = pr.create_geographic_entity(GeographicEntityType.STATE, "St", country.entity_id)
    county = pr.create_geographic_entity(GeographicEntityType.COUNTY, "Cn", state.entity_id)
    city = pr.create_geographic_entity(GeographicEntityType.CITY, "Ci", county.entity_id)
    street = pr.create_geographic_entity(GeographicEntityType.STREET, "Sr", city.entity_id)
    building = pr.create_geographic_entity(GeographicEntityType.BUILDING, "Bu", street.entity_id)
    return [pr.create_geographic_entity(GeographicEntityType.SPACE, name, building.entity_id) for name in space_names]


# ── Permanent memberships ───────────────────────────────────────────────


def test_permanent_membership_is_explicitly_stored_and_persists_across_movement():
    pr = PlanetaryRuntime()
    space_a, space_b = _build_chain(pr, "A", "B")
    alice = _register(pr, home_space_id=space_a.entity_id)

    other = pr.create_society(name="Guild")
    pr.membership_registry.add(alice.actor_id, other.society.society_id, role="member")

    assert other.society.society_id in pr.membership_registry.societies_for_actor(alice.actor_id)

    pr.move_actor(alice.actor_id, space_b.entity_id)
    assert other.society.society_id in pr.membership_registry.societies_for_actor(alice.actor_id), (
        "permanent membership must persist regardless of movement"
    )


def test_temporary_membership_never_modifies_permanent_membership():
    pr = PlanetaryRuntime()
    (space_a,) = _build_chain(pr, "A")
    alice = _register(pr, home_space_id=space_a.entity_id)
    home_society_id = pr.society.society_id

    permanent_before = set(pr.membership_registry.societies_for_actor(alice.actor_id))
    assert home_society_id in permanent_before

    pr.move_actor(alice.actor_id, space_a.entity_id)  # re-enter, no-op move
    permanent_after = set(pr.membership_registry.societies_for_actor(alice.actor_id))
    assert permanent_after == permanent_before, "temporary grant/revoke must never touch permanent state"


# ── Temporary memberships ───────────────────────────────────────────────


def test_temporary_membership_granted_on_entering_associated_space():
    pr = PlanetaryRuntime()
    home_space, other_space = _build_chain(pr, "Home", "Other")
    alice = _register(pr, home_space_id=home_space.entity_id)

    other = pr.create_society(name="Neighbors")
    pr.host_society(other_space.entity_id, other.society.society_id)

    assert other.society.society_id not in pr.membership_governor.temporary_societies_for_actor(alice.actor_id)
    pr.move_actor(alice.actor_id, other_space.entity_id)
    assert other.society.society_id in pr.membership_governor.temporary_societies_for_actor(alice.actor_id)


def test_temporary_membership_not_granted_when_already_permanent():
    pr = PlanetaryRuntime()
    (home_space,) = _build_chain(pr, "Home")
    alice = _register(pr, home_space_id=home_space.entity_id)
    home_society_id = pr.society.society_id

    # The Actor's home Society is already hosted at home_space — re-entering
    # it must not create a redundant temporary membership for a Society the
    # Actor already holds permanently.
    pr.move_actor(alice.actor_id, home_space.entity_id)
    assert home_society_id not in pr.membership_governor.temporary_societies_for_actor(alice.actor_id)
    assert home_society_id in pr.effective_societies(alice.actor_id)


def test_temporary_membership_revoked_immediately_on_leaving_space():
    pr = PlanetaryRuntime()
    home_space, hosted_space, elsewhere = _build_chain(pr, "Home", "Hosted", "Elsewhere")
    alice = _register(pr, home_space_id=home_space.entity_id)

    other = pr.create_society(name="Visited")
    pr.host_society(hosted_space.entity_id, other.society.society_id)

    pr.move_actor(alice.actor_id, hosted_space.entity_id)
    assert other.society.society_id in pr.membership_governor.temporary_societies_for_actor(alice.actor_id)

    pr.move_actor(alice.actor_id, elsewhere.entity_id)
    assert other.society.society_id not in pr.membership_governor.temporary_societies_for_actor(alice.actor_id)


# ── Registration (Actor Registration Invariant) ─────────────────────────


def test_register_actor_with_no_home_space_uses_default_bootstrap_space():
    pr = PlanetaryRuntime()
    assert pr.default_bootstrap_space_id is not None

    alice = _register(pr)
    presence = pr.presence.current(alice.actor_id)
    assert presence is not None and presence.is_open()
    assert presence.space_id == pr.default_bootstrap_space_id
    pr.geo_registry.validate_society_has_space(pr.society.society_id)


def test_register_actor_with_explicit_home_space_hosts_and_places_actor_there():
    pr = PlanetaryRuntime()
    (space,) = _build_chain(pr, "Custom")
    alice = _register(pr, home_space_id=space.entity_id)

    presence = pr.presence.current(alice.actor_id)
    assert presence is not None and presence.space_id == space.entity_id
    assert pr.geo_registry.entity_for_society(pr.society.society_id).entity_id == space.entity_id


def test_register_actor_with_explicit_society_id_enforces_same_invariants():
    """Registration Entry Points: register_actor(society_id=...) is the
    SAME canonical workflow api/routes/actors.py and memberships.py now
    delegate to for a non-home Society — it must enforce the identical
    invariants a home registration gets, not a weaker bypass path."""
    pr = PlanetaryRuntime()
    other = pr.create_society(name="Other Society")

    profile = ActorProfile(identity=ActorIdentity(name="Non-home Actor", actor_type=ActorType.HUMAN))
    state = pr.register_actor(profile, society_id=other.society.society_id)

    presence = pr.presence.current(state.actor_id)
    assert presence is not None and presence.is_open()
    pr.geo_registry.validate_society_has_space(other.society.society_id)
    assert other.society.society_id in pr.membership_registry.societies_for_actor(state.actor_id)


def test_register_actor_rejects_unknown_society_id_without_partial_state():
    pr = PlanetaryRuntime()
    profile = ActorProfile(identity=ActorIdentity(name="Ghost2", actor_type=ActorType.HUMAN))
    actor_id = profile.identity.actor_id

    with pytest.raises(LookupError):
        pr.register_actor(profile, society_id="does-not-exist")

    assert pr.societies_for_actor(actor_id) == ()
    assert pr.presence.current(actor_id) is None


def test_register_actor_rejects_invalid_home_space_without_partial_state():
    pr = PlanetaryRuntime()
    bogus_actor_id = None
    profile = ActorProfile(identity=ActorIdentity(name="Ghost", actor_type=ActorType.HUMAN))
    bogus_actor_id = profile.identity.actor_id

    with pytest.raises(ValueError):
        pr.register_actor(profile, home_space_id="not-a-real-space")

    assert pr._society_runtime.get_actor(bogus_actor_id) is None, (
        "a failed registration must not leave a half-registered Actor behind"
    )
    assert pr.presence.current(bogus_actor_id) is None


def test_register_actor_guarantees_home_society_has_a_space_on_success():
    pr = PlanetaryRuntime()
    _register(pr, name="Bob")
    # The core invariant: after ANY successful registration, every
    # associated Society (here, just the home Society) has a Space.
    pr.geo_registry.validate_society_has_space(pr.society.society_id)


# ── Movement ─────────────────────────────────────────────────────────────


def test_movement_updates_presence_timeline():
    pr = PlanetaryRuntime()
    space_a, space_b = _build_chain(pr, "A", "B")
    alice = _register(pr, home_space_id=space_a.entity_id)

    assert pr.presence.current(alice.actor_id).space_id == space_a.entity_id
    pr.move_actor(alice.actor_id, space_b.entity_id)
    assert pr.presence.current(alice.actor_id).space_id == space_b.entity_id


def test_movement_publishes_membership_lifecycle_events_to_context_stream():
    pr = PlanetaryRuntime()
    home_space, hosted_space = _build_chain(pr, "Home", "Hosted")
    alice = _register(pr, home_space_id=home_space.entity_id)

    other = pr.create_society(name="Hosted Society")
    pr.host_society(hosted_space.entity_id, other.society.society_id)

    before = pr.context_stream.event_count
    pr.move_actor(alice.actor_id, hosted_space.entity_id)
    new_events = pr.context_stream.events(limit=pr.context_stream.event_count - before)

    assert any(e.event_type is ContextEventType.TEMPORARY_MEMBERSHIP_GRANTED for e in new_events)


# ── Membership transitions ──────────────────────────────────────────────


def test_moving_between_two_societies_revokes_old_and_grants_new():
    pr = PlanetaryRuntime()
    home_space, space_x, space_y = _build_chain(pr, "Home", "X", "Y")
    alice = _register(pr, home_space_id=home_space.entity_id)

    soc_x = pr.create_society(name="Society X")
    soc_y = pr.create_society(name="Society Y")
    pr.host_society(space_x.entity_id, soc_x.society.society_id)
    pr.host_society(space_y.entity_id, soc_y.society.society_id)

    pr.move_actor(alice.actor_id, space_x.entity_id)
    assert soc_x.society.society_id in pr.membership_governor.temporary_societies_for_actor(alice.actor_id)

    pr.move_actor(alice.actor_id, space_y.entity_id)
    temp = pr.membership_governor.temporary_societies_for_actor(alice.actor_id)
    assert soc_y.society.society_id in temp
    assert soc_x.society.society_id not in temp


def test_moving_to_space_with_no_society_grants_nothing():
    pr = PlanetaryRuntime()
    home_space, empty_space = _build_chain(pr, "Home", "Empty")
    alice = _register(pr, home_space_id=home_space.entity_id)

    pr.move_actor(alice.actor_id, empty_space.entity_id)
    assert pr.membership_governor.temporary_societies_for_actor(alice.actor_id) == frozenset()


# ── Validation failures ──────────────────────────────────────────────────


def test_validate_society_has_space_raises_for_unhosted_society():
    pr = PlanetaryRuntime()
    orphan = pr.create_society(name="Orphan")
    pr._geo_registry.unhost_society(
        pr._geo_registry.entity_for_society(orphan.society.society_id).entity_id,
        orphan.society.society_id,
    )
    with pytest.raises(ValueError):
        pr.geo_registry.validate_society_has_space(orphan.society.society_id)


def test_validate_society_has_space_raises_when_hosted_entity_has_no_space_descendant():
    pr = PlanetaryRuntime()
    planet = pr.create_geographic_entity(GeographicEntityType.PLANET, "P", None)
    country = pr.create_geographic_entity(GeographicEntityType.COUNTRY, "Co", planet.entity_id)
    # A Society hosted at a Country with no Space anywhere beneath it must
    # still fail validation — being "hosted" somewhere isn't enough.
    orphan = pr.create_society(name="Orphan")
    pr.host_society(country.entity_id, orphan.society.society_id)
    with pytest.raises(ValueError):
        pr.geo_registry.validate_society_has_space(orphan.society.society_id)


def test_validate_society_has_space_passes_for_hosted_society_with_a_space():
    pr = PlanetaryRuntime()
    (space,) = _build_chain(pr, "S")
    soc = pr.create_society(name="Housed")
    pr.host_society(space.entity_id, soc.society.society_id)
    pr.geo_registry.validate_society_has_space(soc.society.society_id)  # must not raise


# ── Effective membership computation ─────────────────────────────────────


def test_effective_societies_is_union_of_permanent_and_temporary_preserving_distinction():
    pr = PlanetaryRuntime()
    home_space, hosted_space = _build_chain(pr, "Home", "Hosted")
    alice = _register(pr, home_space_id=home_space.entity_id)
    home_society_id = pr.society.society_id

    guild = pr.create_society(name="Guild")
    pr.membership_registry.add(alice.actor_id, guild.society.society_id, role="member")

    visited = pr.create_society(name="Visited")
    pr.host_society(hosted_space.entity_id, visited.society.society_id)
    pr.move_actor(alice.actor_id, hosted_space.entity_id)

    permanent = set(pr.membership_registry.societies_for_actor(alice.actor_id))
    temporary = pr.membership_governor.temporary_societies_for_actor(alice.actor_id)
    effective = pr.effective_societies(alice.actor_id)

    assert permanent == {home_society_id, guild.society.society_id}
    assert temporary == frozenset({visited.society.society_id})
    assert effective == permanent | temporary
    # The two kinds stay independently queryable — never flattened away.
    assert visited.society.society_id not in permanent
    assert guild.society.society_id not in temporary


# ── Coordination Boundary: temporary membership = real participation ────
#
# "Societies coordinate the behavior and interactions of all actors
# currently participating in the society, whether through permanent
# membership or temporary membership derived from physical presence."
# A temporarily-present Actor must become a real coordination participant
# in that Society's SocietyRuntime (active_actors(), observation
# visibility) — not only an entry in effective_societies().


def test_entering_a_hosting_space_makes_actor_a_coordination_participant():
    pr = PlanetaryRuntime()
    home_space, visited_space = _build_chain(pr, "Home", "Visited")
    alice = _register(pr, home_space_id=home_space.entity_id)

    visited = pr.create_society(name="Visited Society")
    pr.host_society(visited_space.entity_id, visited.society.society_id)
    assert visited.get_actor(alice.actor_id) is None

    pr.move_actor(alice.actor_id, visited_space.entity_id)
    participant = visited.get_actor(alice.actor_id)
    assert participant is not None
    assert alice.actor_id in {a.actor_id for a in visited.active_actors()}
    # Shared cognition — never a second ActorRuntimeState for the same Actor.
    home = pr._home_society_runtime(alice.actor_id)
    assert participant is home.get_actor(alice.actor_id)


def test_leaving_a_hosting_space_removes_coordination_participation():
    pr = PlanetaryRuntime()
    home_space, visited_space = _build_chain(pr, "Home", "Visited")
    alice = _register(pr, home_space_id=home_space.entity_id)

    visited = pr.create_society(name="Visited Society")
    pr.host_society(visited_space.entity_id, visited.society.society_id)
    pr.move_actor(alice.actor_id, visited_space.entity_id)
    assert visited.get_actor(alice.actor_id) is not None

    pr.move_actor(alice.actor_id, home_space.entity_id)
    assert visited.get_actor(alice.actor_id) is None


def test_temporary_participation_grants_observation_visibility():
    from src.monkey_brain.kernel.society.world import WorldEntity, WorldEntityType

    pr = PlanetaryRuntime()
    home_space, visited_space = _build_chain(pr, "Home", "Visited")
    alice = _register(pr, home_space_id=home_space.entity_id)

    visited = pr.create_society(name="Visited Society")
    pr.host_society(visited_space.entity_id, visited.society.society_id)
    pr.add_world_entity(
        WorldEntity(
            name="Visited Asset",
            entity_type=WorldEntityType.ENTITY,
            owner_society_id=visited.society.society_id,
        )
    )

    assert pr._effective_is_member(alice.actor_id, visited.society.society_id) is False
    pr.move_actor(alice.actor_id, visited_space.entity_id)
    assert pr._effective_is_member(alice.actor_id, visited.society.society_id) is True


@pytest.mark.asyncio
async def test_dual_participation_never_double_ticks_cognition():
    pr = PlanetaryRuntime()
    home_space, visited_space = _build_chain(pr, "Home", "Visited")
    alice = _register(pr, home_space_id=home_space.entity_id)

    visited = pr.create_society(name="Visited Society")
    pr.host_society(visited_space.entity_id, visited.society.society_id)
    pr.move_actor(alice.actor_id, visited_space.entity_id)
    assert visited.get_actor(alice.actor_id) is not None  # participant of both now

    # _build_chain() builds its own Planet ("P"), a separate tree from
    # pr._default_planet (the synthetic bootstrap chain PlanetaryRuntime()
    # creates when the geo registry starts empty) -- walking from
    # _default_planet here never reaches home_space/visited_space at all,
    # so alice is never ticked. Walk from home_space's real ancestor
    # Planet instead.
    planet = pr.geo_registry.ancestor_of_type(home_space.entity_id, GeographicEntityType.PLANET)
    cycle_count_before = alice.cycle_count
    await pr.tick_geographic_entity(planet.entity_id)
    assert alice.cycle_count == cycle_count_before + 1, (
        "an Actor participating in two Societies at once must still tick exactly once"
    )


def test_temporary_revoked_by_supersession_keeps_participant_if_now_permanent():
    """reconcile() revokes a temporary entry once it's covered by a new
    permanent membership — that must not evict the Actor from the
    Society's coordination, since it's now there legitimately anyway."""
    pr = PlanetaryRuntime()
    home_space, visited_space = _build_chain(pr, "Home", "Visited")
    alice = _register(pr, home_space_id=home_space.entity_id)

    visited = pr.create_society(name="Visited Society")
    pr.host_society(visited_space.entity_id, visited.society.society_id)
    pr.move_actor(alice.actor_id, visited_space.entity_id)

    pr.membership_registry.add(alice.actor_id, visited.society.society_id, role="member")
    pr.membership_governor.reconcile(alice.actor_id)

    assert visited.society.society_id not in pr.membership_governor.temporary_societies_for_actor(alice.actor_id)
    assert visited.get_actor(alice.actor_id) is not None, (
        "must remain a participant — the membership became permanent, it didn't disappear"
    )
