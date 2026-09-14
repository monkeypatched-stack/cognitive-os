"""Tests for Membership as a First-Class Runtime Resource: the Membership
view, role assignment, lifecycle transitions, trust, delegation, policy/
permission/capability/constraint resolution, and full timeline auditability.
"""

from __future__ import annotations

from src.monkey_brain.kernel.society.membership import SocietyMembershipRegistry
from src.monkey_brain.kernel.society.delegation import DelegationRegistry
from src.monkey_brain.kernel.society.governance import (
    SocietyGovernanceEngine,
    GovernancePolicy,
    Permission,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
)
from src.monkey_brain.kernel.timeline.entry import TimelineKind
from src.monkey_brain.kernel.timeline.store import TimelineStore


def _register(pr, name="Alice"):
    return pr.register_actor(ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN)))


# ── Multiple concurrent memberships ───────────────────────────────────────


def test_multiple_concurrent_memberships_per_actor():
    reg = SocietyMembershipRegistry()
    reg.add("alice", "society-a", role="member")
    reg.add("alice", "society-b", role="engineer")
    memberships = reg.memberships_for_actor("alice")
    assert len(memberships) == 2
    assert {m.society_id for m in memberships} == {"society-a", "society-b"}
    assert all(m.is_active() for m in memberships)


def test_home_registration_is_a_real_membership():
    pr = PlanetaryRuntime()
    alice = _register(pr)
    memberships = pr.membership_registry.memberships_for_actor(alice.actor_id)
    assert len(memberships) == 1
    assert memberships[0].is_active()


# ── Roles ─────────────────────────────────────────────────────────────────


def test_role_assign_and_remove_preserves_history():
    reg = SocietyMembershipRegistry()
    record = reg.add("alice", "soc1", role="member")
    mid = record.membership_id

    reg.assign_role(mid, "engineer")
    reg.remove_role(mid, "member")

    current = reg.get_membership(mid)
    assert current.roles == ("engineer",)

    history = reg.history_for_actor("alice")
    assert len(history) == 3
    assert [h.metadata.get("event") for h in history] == [
        "created",
        "role_assigned",
        "role_removed",
    ]
    # Every prior role state stays queryable — history isn't lost.
    assert history[0].roles == ("member",)
    assert history[1].roles == ("member", "engineer")
    assert history[2].roles == ("engineer",)


def test_assigning_duplicate_role_is_a_noop():
    reg = SocietyMembershipRegistry()
    record = reg.add("alice", "soc1", role="member")
    mid = record.membership_id
    reg.assign_role(mid, "member")
    history = reg.history_for_actor("alice")
    assert len(history) == 1  # no duplicate event


# ── Lifecycle ─────────────────────────────────────────────────────────────


def test_lifecycle_transitions_fully_auditable():
    reg = SocietyMembershipRegistry()
    record = reg.add("alice", "soc1")
    mid = record.membership_id

    reg.set_status(mid, "suspended", reason="investigation")
    reg.set_status(mid, "active")
    reg.set_status(mid, "terminated", reason="left org")

    history = reg.history_for_actor("alice")
    events = [h.metadata.get("event") for h in history]
    assert events == ["created", "suspended", "activated", "terminated"]
    assert history[1].reason == "investigation"
    assert history[3].reason == "left org"
    assert reg.get_membership(mid).status == "terminated"
    assert not reg.is_member("alice", "soc1")


# ── Policy/permission/capability/constraint resolution ────────────────────


def test_resolve_permissions_and_policies_against_real_governance():
    reg = SocietyMembershipRegistry()
    record = reg.add("alice", "soc1")
    mid = record.membership_id

    gov = SocietyGovernanceEngine()
    gov.add_policy(GovernancePolicy(name="budget", priority=5))
    gov.grant_permission(Permission(actor_id="alice", resource="cart", action="checkout"))

    permissions = reg.resolve_permissions(mid, governance=gov)
    assert "cart:checkout" in permissions

    policies = reg.resolve_policies(mid, governance=gov)
    assert [p.name for p in policies] == ["budget"]

    constraints = reg.resolve_constraints(mid, governance=gov)
    assert len(constraints) == 1


def test_terminated_membership_immediately_loses_governance_surface():
    reg = SocietyMembershipRegistry()
    record = reg.add("alice", "store", role="employee")
    mid = record.membership_id
    gov = SocietyGovernanceEngine()
    gov.add_policy(GovernancePolicy(name="employee discount", metadata={"required_role": "employee"}))
    gov.grant_permission(Permission(actor_id="alice", resource="store", action="loyalty_price"))

    assert reg.resolve_policies(mid, governance=gov)
    assert "store:loyalty_price" in reg.resolve_permissions(mid, governance=gov)

    reg.set_status(mid, "terminated", reason="membership ended")

    assert reg.resolve_policies(mid, governance=gov) == ()
    assert reg.resolve_permissions(mid, governance=gov) == ()
    assert reg.resolve_constraints(mid, governance=gov) == ()


def test_expired_membership_is_not_active():
    import time
    from src.monkey_brain.kernel.society.membership import Membership

    expired = Membership(
        membership_id="m",
        actor_id="alice",
        society_id="senior-program",
        end_time=time.time() - 1,
    )
    assert not expired.is_active()


def test_policy_can_require_membership_role():
    reg = SocietyMembershipRegistry()
    employee = reg.add("alice", "store", role="employee")
    customer = reg.add("bob", "store", role="customer")
    gov = SocietyGovernanceEngine()
    gov.add_policy(
        GovernancePolicy(
            name="employee discount",
            metadata={"required_role": "employee"},
        )
    )

    assert len(reg.resolve_policies(employee.membership_id, governance=gov)) == 1
    assert reg.resolve_policies(customer.membership_id, governance=gov) == ()


def test_resolve_capabilities_from_actor_profile():
    from src.monkey_brain.kernel.society.domain import ActorCapability, CapabilityLevel

    reg = SocietyMembershipRegistry()
    record = reg.add("alice", "soc1")
    profile = ActorProfile(
        identity=ActorIdentity(name="Alice", actor_type=ActorType.HUMAN),
        capabilities=(ActorCapability(name="drive", level=CapabilityLevel.EXPERT),),
    )
    capabilities = reg.resolve_capabilities(record.membership_id, actor_profile=profile)
    assert capabilities == ("drive",)


# ── Trust ─────────────────────────────────────────────────────────────────


def test_trust_update_writes_through_and_appends_timeline_event():
    reg = SocietyMembershipRegistry()
    record = reg.add("alice", "soc1")
    mid = record.membership_id
    gov = SocietyGovernanceEngine()

    updated = reg.update_trust(mid, 0.9, governance=gov)
    assert updated.trust_score == 0.9
    assert gov.get_trust("alice").trust_score == 0.9

    history = reg.history_for_actor("alice")
    assert history[-1].metadata.get("event") == "trust_updated"


# ── Delegation ────────────────────────────────────────────────────────────


def test_delegation_grant_revoke_validity_and_effective_permissions():
    reg = SocietyMembershipRegistry()
    record = reg.add("alice", "soc1", role="parent")
    mid = record.membership_id
    delegations = DelegationRegistry(membership_registry=reg)

    d = delegations.grant(mid, "bob", ("cart:checkout",), reason="vacation coverage")
    assert delegations.is_valid(d.delegation_id)
    assert delegations.effective_delegated_permissions("bob") == ("cart:checkout",)

    delegations.revoke(d.delegation_id, reason="vacation over")
    assert not delegations.is_valid(d.delegation_id)
    assert delegations.effective_delegated_permissions("bob") == ()

    history = reg.history_for_actor("alice")
    events = [h.metadata.get("event") for h in history]
    assert "delegation_granted" in events
    assert "delegation_revoked" in events


def test_delegation_respects_validity_window():
    import time

    reg = SocietyMembershipRegistry()
    record = reg.add("alice", "soc1")
    delegations = DelegationRegistry(membership_registry=reg)
    d = delegations.grant(record.membership_id, "bob", ("x",), valid_until=time.time() - 1)
    assert not delegations.is_valid(d.delegation_id)


# ── Every mutator produces a timeline entry ───────────────────────────────


def test_every_mutator_produces_a_distinct_timeline_row():
    store = TimelineStore()
    reg = SocietyMembershipRegistry(store=store)
    record = reg.add("timeline-actor", "soc1")
    mid = record.membership_id

    reg.assign_role(mid, "engineer")
    reg.set_status(mid, "suspended")
    reg.set_status(mid, "active")
    gov = SocietyGovernanceEngine()
    reg.update_trust(mid, 0.7, governance=gov)

    rows = store.query("timeline-actor", TimelineKind.MEMBERSHIP)
    assert len(rows) == 5
    # Only the latest row is open; every prior one is closed.
    assert sum(1 for r in rows if r.is_open()) == 1


# ── Discovery methods ──────────────────────────────────────────────────────


def test_memberships_for_society_and_by_role():
    reg = SocietyMembershipRegistry()
    reg.add("alice", "soc1", role="engineer")
    reg.add("bob", "soc1", role="manager")
    reg.add("carol", "soc2", role="engineer")

    soc1_members = reg.memberships_for_society("soc1")
    assert {m.actor_id for m in soc1_members} == {"alice", "bob"}

    engineers = reg.memberships_by_role("engineer")
    assert {m.actor_id for m in engineers} == {"alice", "carol"}


def test_active_memberships_excludes_terminated():
    reg = SocietyMembershipRegistry()
    r1 = reg.add("alice", "soc1")
    reg.add("bob", "soc1")
    reg.set_status(r1.membership_id, "terminated")

    active = reg.active_memberships()
    assert "alice" not in {m.actor_id for m in active}
    assert "bob" in {m.actor_id for m in active}


# ── Society Graph fix regression tests ──────────────────────────────────────


def _affiliations_for(pr, actor_id):
    """Reach the same AffiliationManager _mirror_membership_affiliation and
    _unmirror_membership_affiliation read/write — mirrors those methods'
    own lookup exactly, since there's no public accessor for it."""
    for sr in pr._societies_for(actor_id):
        state = sr.get_actor(actor_id)
        if state is not None and getattr(state, "actor_runtime", None) is not None:
            affiliations = getattr(state.actor_runtime, "affiliations", None)
            if affiliations is not None:
                return affiliations
    return None


def test_create_society_and_register_actor_produces_real_membership():
    pr = PlanetaryRuntime()
    club = pr.create_society("Book Club", society_type="community")
    alice = pr.register_actor(
        ActorProfile(identity=ActorIdentity(name="Alice", actor_type=ActorType.HUMAN)),
        society_id=club.society.society_id,
    )
    memberships = pr.membership_registry.memberships_for_actor(alice.actor_id)
    assert len(memberships) == 1
    assert memberships[0].society_id == club.society.society_id
    assert memberships[0].is_active()


def test_join_society_creates_membership_and_affiliation_mirror():
    pr = PlanetaryRuntime()
    alice = _register(pr)
    club = pr.create_society("Book Club", society_type="community")

    assert pr.join_society(alice.actor_id, club.society.society_id, role="member")

    assert pr.membership_registry.is_member(alice.actor_id, club.society.society_id)
    affiliations = _affiliations_for(pr, alice.actor_id)
    assert affiliations is not None
    mirrored = [a for a in affiliations.by_target(club.society.society_id) if a.affiliation_type == "member_of"]
    assert len(mirrored) == 1


def test_leave_society_removes_membership_and_unmirrors_affiliation():
    pr = PlanetaryRuntime()
    alice = _register(pr)
    club = pr.create_society("Book Club", society_type="community")
    pr.join_society(alice.actor_id, club.society.society_id, role="member")

    assert pr.leave_society(alice.actor_id, club.society.society_id)

    assert not pr.membership_registry.is_member(alice.actor_id, club.society.society_id)
    affiliations = _affiliations_for(pr, alice.actor_id)
    mirrored = [a for a in affiliations.by_target(club.society.society_id) if a.affiliation_type == "member_of"]
    assert mirrored == []


def test_terminate_membership_lifecycle_action_unmirrors_affiliation():
    """Regression test for the /memberships/{id}/terminate route bypass —
    reproduces _set_status's own logic against pr directly since the route
    itself needs a FastAPI Request."""
    pr = PlanetaryRuntime()
    alice = _register(pr)
    club = pr.create_society("Book Club", society_type="community")
    pr.join_society(alice.actor_id, club.society.society_id, role="member")
    membership = pr.membership_registry.get_membership(
        pr.membership_registry.memberships_for_society(club.society.society_id)[0].membership_id,
    )

    m = pr.membership_registry.set_status(membership.membership_id, "terminated")
    pr._unmirror_membership_affiliation(m.actor_id, m.society_id)

    affiliations = _affiliations_for(pr, alice.actor_id)
    mirrored = [a for a in affiliations.by_target(club.society.society_id) if a.affiliation_type == "member_of"]
    assert mirrored == []


def test_delete_society_preserves_actors_and_geography_removes_only_society_and_relationships():
    """Replicates DELETE /societies/{id}'s route logic directly against pr
    (the route itself needs a FastAPI Request) to prove requirement 13:
    deleting a Society removes only the Society and its relationships,
    never the Humans/Enterprises or Geography it referenced."""
    from src.monkey_brain.kernel.geography.entity import GeographicEntityType

    pr = PlanetaryRuntime()
    alice = _register(pr)
    bob = _register(pr, name="Bob")
    alice_home = pr.societies_for_actor(alice.actor_id)[0]

    planet = pr._geo_registry.create(GeographicEntityType.PLANET, "Test Planet")
    country = pr._geo_registry.create(GeographicEntityType.COUNTRY, "Test Country", parent_id=planet.entity_id)
    state_ = pr._geo_registry.create(GeographicEntityType.STATE, "Test State", parent_id=country.entity_id)
    county = pr._geo_registry.create(GeographicEntityType.COUNTY, "Test County", parent_id=state_.entity_id)
    city = pr._geo_registry.create(GeographicEntityType.CITY, "Test City", parent_id=county.entity_id)

    club = pr.create_society("Book Club", society_type="community")
    club_id = club.society.society_id
    pr.host_society(city.entity_id, club_id)
    assert pr.join_society(alice.actor_id, club_id, role="member")
    assert pr.join_society(bob.actor_id, club_id, role="member")

    # Replicate delete_society's route body (api/routes/societies.py) against pr.
    sr = pr.get_society_runtime(club_id)
    for state in sr.all_actors():
        sr.unregister_actor(state.actor_id)
    memberships = pr.membership_registry.memberships_for_society(club_id)
    for membership in memberships:
        pr.membership_registry.set_status(membership.membership_id, "terminated", reason="society deleted")
        pr._unmirror_membership_affiliation(membership.actor_id, club_id)
    hosting_entity = pr.entity_for_society(club_id)
    if hosting_entity is not None:
        pr.unhost_society(hosting_entity.entity_id, club_id)
    pr._societies.pop(club_id, None)

    # The Society itself is gone.
    assert pr.get_society_runtime(club_id) is None
    # Every membership was terminated, with no lingering affiliation mirror.
    for membership in memberships:
        assert not pr.membership_registry.is_member(membership.actor_id, club_id)
        affiliations = _affiliations_for(pr, membership.actor_id)
        assert [a for a in affiliations.by_target(club_id) if a.affiliation_type == "member_of"] == []
    # Actors are preserved — still real, still members of their real home society.
    assert pr.societies_for_actor(alice.actor_id) == (alice_home,)
    assert pr.get_actor_runtime(alice.actor_id) is not None
    assert pr.get_actor_runtime(bob.actor_id) is not None
    # Geography is preserved — only unhosted, never deleted.
    assert pr._geo_registry.get(city.entity_id) is not None
    assert club_id not in pr._geo_registry.get(city.entity_id).hosted_society_ids
