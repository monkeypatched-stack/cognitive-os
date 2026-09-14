"""CCB-200..203 — household society, shared resources, and membership gates."""

from __future__ import annotations

from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
from src.monkey_brain.kernel.learn.memory.graph_adapter import (
    KnowledgeGraphMemoryAdapter,
)
from src.monkey_brain.kernel.learn.memory.manager import MemoryManager
from src.monkey_brain.kernel.learn.memory.vector_backend import InMemoryVectorBackend
from src.monkey_brain.kernel.pipeline.belief_state import Goal
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
from src.monkey_brain.kernel.pipeline.planning.context_engine import (
    ContextConstructionEngine,
)
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.governance import GovernancePolicy, Permission
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def _actor(pr: PlanetaryRuntime, name: str):
    return pr.register_actor(
        ActorProfile(
            identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN),
        )
    )


def _household():
    pr = PlanetaryRuntime()
    actors = [_actor(pr, name) for name in ("Parent", "Teen", "Grandparent", "Infant")]
    household = pr.create_society(
        "The Household",
        society_type="household",
        always_active=True,
    )
    for actor in actors:
        pr.join_society(actor.actor_id, household.society_id)
    household.update_shared_resources(
        budget={"available": 25.0, "currency": "USD"},
        pantry={"eggs": 3, "milk": 2},
        shopping_list=["diapers"],
    )
    return pr, actors, household


def _context_engine(pr):
    kg = KnowledgeGraph()
    mm = MemoryManager(InMemoryVectorBackend(), KnowledgeGraphMemoryAdapter(kg))
    return ContextConstructionEngine(planetary_runtime=pr, memory_manager=mm, knowledge_graph=kg)


def test_household_reasoning_exposes_shared_resources_and_merged_goals():
    pr, actors, household = _household()
    household.add_shared_goal("keep the household stocked")
    household.add_shared_goal("stay within the weekly budget")

    context = _context_engine(pr).build(
        actors[1].actor_id,
        Goal(name="buy diapers", description="for the household"),
    )

    assert context.metadata["shared_resources"]["budget"]["available"] == 25.0
    assert context.metadata["shared_resources"]["pantry"] == {"eggs": 3, "milk": 2}
    assert context.metadata["shared_resources"]["shopping_list"] == ["diapers"]
    assert context.metadata["shared_goals"] == (
        "keep the household stocked",
        "stay within the weekly budget",
    )


def test_budget_policy_is_available_to_the_llm_planner():
    pr, actors, household = _household()
    household.governance.add_policy(
        GovernancePolicy(
            name="essential over luxury",
            rules=("essential items have priority over luxury items",),
            priority=10,
        )
    )
    context = _context_engine(pr).build(
        actors[0].actor_id,
        Goal(name="buy diapers and ice cream"),
    )
    prompt = LLMPlanner(backend=object())._build_prompt(context.goal, [], context)

    assert "essential over luxury" in prompt
    assert "budget={'available': 25.0" in prompt


def test_leaving_household_removes_shared_resources_and_goals_from_context():
    pr, actors, household = _household()
    household.add_shared_goal("keep the household stocked")
    teen = actors[1]

    pr.leave_society(teen.actor_id, household.society_id)
    context = _context_engine(pr).build(teen.actor_id, Goal(name="buy milk"))

    assert context.metadata["shared_resources"] == {}
    assert context.metadata["shared_goals"] == ()
    assert household.society_id not in pr.membership_registry.societies_for_actor(teen.actor_id)


def test_guest_can_view_list_but_cannot_spend_household_wallet():
    pr, actors, household = _household()
    grandparent = actors[2]
    membership = next(
        m
        for m in pr.membership_registry.memberships_for_actor(grandparent.actor_id)
        if m.society_id == household.society_id
    )
    pr.membership_registry.remove(grandparent.actor_id, household.society_id)
    pr.join_society(grandparent.actor_id, household.society_id, role="guest")
    guest = next(
        m
        for m in pr.membership_registry.memberships_for_actor(grandparent.actor_id)
        if m.society_id == household.society_id and m.is_active()
    )
    governance = pr.governance_for(household.society_id)
    governance.grant_permission(
        Permission(
            actor_id=grandparent.actor_id,
            resource="household_shopping_list",
            action="view",
        )
    )

    permissions = pr.membership_registry.resolve_permissions(guest.membership_id, governance)
    assert "household_shopping_list:view" in permissions
    assert "household_wallet:spend" not in permissions
    assert pr.check_permission(grandparent.actor_id, "household_shopping_list", "view")

    pr.leave_society(grandparent.actor_id, household.society_id)
    assert not pr.check_permission(grandparent.actor_id, "household_shopping_list", "view")
