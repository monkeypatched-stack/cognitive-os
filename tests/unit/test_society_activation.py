"""Tests for the Society as Organizational Context refactor: real
multi-membership (an actor may be MEMBER_OF several societies at once,
without duplicating cognition), Team's one-team-per-society rule extended
across societies, dynamic goal-driven Society Activation, deterministic
policy-precedence, and independence from the physical geography graph.
"""

from __future__ import annotations

import dataclasses

from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
)
from src.monkey_brain.kernel.society.membership import SocietyMembershipRegistry
from src.monkey_brain.kernel.society.activation import SocietyActivationEngine
from src.monkey_brain.kernel.society.governance import GovernancePolicy
from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.relationships import RelationshipKind


def _new_actor(pr: PlanetaryRuntime, name: str = "Alice") -> str:
    profile = ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN))
    return pr.register_actor(profile).actor_id


# ── Multi-membership without duplicating cognition ───────────────────────


def test_join_society_does_not_duplicate_cognition():
    pr = PlanetaryRuntime()
    actor_id = _new_actor(pr)
    home = pr._home_society_runtime(actor_id)
    assert home is not None
    home_state = home.get_actor(actor_id)

    second = pr.create_society("Second Society")
    assert pr.join_society(actor_id, second.society.society_id) is True

    # Same ActorRuntimeState/cognition object in the home society; the
    # second society never got its own registration.
    assert home.get_actor(actor_id) is home_state
    assert second.get_actor(actor_id) is None
    assert sorted(pr.societies_for_actor(actor_id)) == sorted([home.society.society_id, second.society.society_id])


def test_join_society_requires_existing_home_registration():
    pr = PlanetaryRuntime()
    other = pr.create_society("Other")
    assert pr.join_society("nonexistent-actor", other.society.society_id) is False


def test_leave_non_home_society_preserves_cognition():
    pr = PlanetaryRuntime()
    actor_id = _new_actor(pr)
    home = pr._home_society_runtime(actor_id)
    second = pr.create_society("Second Society")
    pr.join_society(actor_id, second.society.society_id)

    assert pr.leave_society(actor_id, second.society.society_id) is True
    assert home.get_actor(actor_id) is not None  # cognition untouched
    assert pr.societies_for_actor(actor_id) == (home.society.society_id,)


def test_leave_home_society_matches_unregister_behavior():
    pr = PlanetaryRuntime()
    actor_id = _new_actor(pr)
    home = pr._home_society_runtime(actor_id)
    assert pr.leave_society(actor_id, home.society.society_id) is True
    assert home.get_actor(actor_id) is None


# ── Team: one-team-per-society, but multiple societies allowed ───────────


def test_actor_can_join_one_team_in_each_of_two_societies():
    pr = PlanetaryRuntime()
    actor_id = _new_actor(pr)
    home = pr._home_society_runtime(actor_id)
    second = pr.create_society("Second Society")
    pr.join_society(actor_id, second.society.society_id)

    team_home = home.create_team("Home Team")
    team_second = second.create_team("Second Team")
    assert home.add_actor_to_team(team_home.team_id, actor_id) is not None
    assert second.add_actor_to_team(team_second.team_id, actor_id) is not None

    teams = {t.name for t in pr.teams_for_actor(actor_id)}
    assert teams == {"Home Team", "Second Team"}


def test_second_team_in_same_society_displaces_first():
    pr = PlanetaryRuntime()
    actor_id = _new_actor(pr)
    home = pr._home_society_runtime(actor_id)
    team_a = home.create_team("Team A")
    team_b = home.create_team("Team B")
    home.add_actor_to_team(team_a.team_id, actor_id)
    home.add_actor_to_team(team_b.team_id, actor_id)

    current = home.team_for_actor(actor_id)
    assert current.team_id == team_b.team_id
    assert not home.get_team(team_a.team_id).has_member(actor_id)


def test_add_actor_to_team_rejects_non_member():
    pr = PlanetaryRuntime()
    actor_id = _new_actor(pr)
    unrelated = pr.create_society("Unrelated")
    team = unrelated.create_team("Team")
    assert unrelated.add_actor_to_team(team.team_id, actor_id) is None


# ── SocietyActivationEngine ────────────────────────────────────────────


def test_always_active_society_always_activates():
    pr = PlanetaryRuntime()
    actor_id = _new_actor(pr)
    home = pr._home_society_runtime(actor_id)
    home._society = dataclasses.replace(home._society, always_active=True)

    result = pr.activate_societies(actor_id, "literally any unrelated goal text")
    assert home.society.society_id in result.activated_society_ids()


def test_tag_matching_society_activates_only_for_matching_goal():
    pr = PlanetaryRuntime()
    actor_id = _new_actor(pr)
    grocery = pr.create_society("Grocery Store")
    grocery._society = dataclasses.replace(grocery._society, activation_tags=("groceries", "grocery"))
    pr.join_society(actor_id, grocery.society.society_id)

    matching = pr.activate_societies(actor_id, "Buy groceries for the week")
    assert grocery.society.society_id in matching.activated_society_ids()

    non_matching = pr.activate_societies(actor_id, "Learn to play piano")
    assert grocery.society.society_id not in non_matching.activated_society_ids()


def test_policy_bundle_precedence_highest_priority_wins():
    pr = PlanetaryRuntime()
    actor_id = _new_actor(pr)
    home = pr._home_society_runtime(actor_id)
    home._society = dataclasses.replace(home._society, always_active=True)
    home.governance.add_policy(GovernancePolicy(name="budget", rules=("max_spend:100",), priority=5))

    second = pr.create_society("Second Society")
    second._society = dataclasses.replace(second._society, always_active=True)
    second.governance.add_policy(GovernancePolicy(name="budget", rules=("max_spend:50",), priority=10))
    pr.join_society(actor_id, second.society.society_id)

    result = pr.activate_societies(actor_id, "irrelevant goal")
    budget_policies = [p for p in result.policy_bundle.policies if p.name == "budget"]
    assert len(budget_policies) == 1
    assert budget_policies[0].priority == 10
    assert budget_policies[0].rules == ("max_spend:50",)


# ── Geography/organization independence ──────────────────────────────────


def test_geography_and_membership_are_independent_graphs():
    pr = PlanetaryRuntime()
    actor_id = _new_actor(pr)
    home = pr._home_society_runtime(actor_id)
    second = pr.create_society("Second Society")
    pr.join_society(actor_id, second.society.society_id)

    country = pr.create_geographic_entity(
        GeographicEntityType.COUNTRY,
        "Testland",
        pr._default_planet.entity_id,
    )
    pr.host_society(country.entity_id, second.society.society_id)

    # Hosting at a geographic entity doesn't touch membership.
    assert sorted(pr.societies_for_actor(actor_id)) == sorted([home.society.society_id, second.society.society_id])
    # And membership changes don't touch geography.
    pr.leave_society(actor_id, second.society.society_id)
    assert pr.entity_for_society(second.society.society_id).entity_id == country.entity_id


def test_hosted_by_relationship_edge_written():
    pr = PlanetaryRuntime()
    society = pr.create_society("Some Society")
    country = pr.create_geographic_entity(
        GeographicEntityType.COUNTRY,
        "Testland",
        pr._default_planet.entity_id,
    )
    pr.host_society(country.entity_id, society.society.society_id)
    rels = pr.relationships.by_kind(RelationshipKind.HOSTED_BY)
    assert any(r.source_id == society.society.society_id and r.target_id == country.entity_id for r in rels)


def test_member_of_relationship_edge_written():
    pr = PlanetaryRuntime()
    actor_id = _new_actor(pr)
    second = pr.create_society("Second Society")
    pr.join_society(actor_id, second.society.society_id)
    rels = pr.relationships.relationships_for(actor_id, kind=RelationshipKind.MEMBER_OF)
    assert any(r.target_id == second.society.society_id for r in rels)


# ── SocietyMembershipRegistry unit tests ──────────────────────────────────


def test_membership_registry_many_to_many():
    registry = SocietyMembershipRegistry()
    registry.add("actor-1", "society-a")
    registry.add("actor-1", "society-b")
    registry.add("actor-2", "society-a")
    assert sorted(registry.societies_for_actor("actor-1")) == ["society-a", "society-b"]
    assert sorted(registry.actors_for_society("society-a")) == ["actor-1", "actor-2"]
    assert registry.is_member("actor-1", "society-b") is True
    registry.remove("actor-1", "society-b")
    assert registry.is_member("actor-1", "society-b") is False
