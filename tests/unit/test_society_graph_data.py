"""Regression tests for the Society Graph fix: real, membership-derived
actor counts (not hardcoded/placeholder figures) and real MEMBER_OF edge
data (the exact affiliation-mirror surface the frontend graph-builder
reads) appearing and disappearing correctly as memberships change.
"""

from __future__ import annotations

from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
)
from src.monkey_brain.api.routes.societies import _society_actor_counts


def _register(pr, name="Alice"):
    return pr.register_actor(ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN)))


def _affiliations_for(pr, actor_id):
    for sr in pr._societies_for(actor_id):
        state = sr.get_actor(actor_id)
        if state is not None and getattr(state, "actor_runtime", None) is not None:
            affiliations = getattr(state.actor_runtime, "affiliations", None)
            if affiliations is not None:
                return affiliations
    return None


def test_society_actor_counts_reflects_membership_not_hardcoded():
    pr = PlanetaryRuntime()
    club = pr.create_society("Book Club", society_type="community")
    club_id = club.society.society_id
    alice = _register(pr)
    bob = _register(pr, name="Bob")

    # Nobody has joined yet.
    assert _society_actor_counts(pr, club_id) == (0, 0)

    pr.join_society(alice.actor_id, club_id, role="member")
    pr.join_society(bob.actor_id, club_id, role="member")
    assert _society_actor_counts(pr, club_id) == (2, 2)

    pr.leave_society(bob.actor_id, club_id)
    # bob's membership is terminated: no longer "current" or "active".
    assert _society_actor_counts(pr, club_id) == (1, 1)


def test_society_actor_counts_distinguishes_active_from_suspended():
    pr = PlanetaryRuntime()
    club = pr.create_society("Book Club", society_type="community")
    club_id = club.society.society_id
    alice = _register(pr)
    pr.join_society(alice.actor_id, club_id, role="member")

    membership = pr.membership_registry.memberships_for_society(club_id)[0]
    pr.membership_registry.set_status(membership.membership_id, "suspended")

    # Still a "current" member (not terminated), but not "active".
    assert _society_actor_counts(pr, club_id) == (1, 0)


def test_member_of_affiliation_present_after_join_absent_after_leave():
    """The exact edge-source data living-world-explorer's worldGraph.tsx
    reads (predicateFor('member_of') -> 'MEMBER_OF') to render the Society
    Graph's Actor<->Society edges."""
    pr = PlanetaryRuntime()
    club = pr.create_society("Book Club", society_type="community")
    club_id = club.society.society_id
    alice = _register(pr)

    # No edge before joining.
    affiliations = _affiliations_for(pr, alice.actor_id)
    assert [a for a in affiliations.by_target(club_id) if a.affiliation_type == "member_of"] == []

    pr.join_society(alice.actor_id, club_id, role="member")
    mirrored = [a for a in affiliations.by_target(club_id) if a.affiliation_type == "member_of"]
    assert len(mirrored) == 1

    pr.leave_society(alice.actor_id, club_id)
    assert [a for a in affiliations.by_target(club_id) if a.affiliation_type == "member_of"] == []
