"""Production Hardening — Plan Invalidation: unit tests for
kernel/pipeline/planning/plan_staleness.py's core check against a bare
KnowledgeGraph, no live server / no pipeline driving needed.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
from src.monkey_brain.kernel.pipeline.belief_state import Plan, PlanStep
from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
    CurrentPlanRecord,
)
from src.monkey_brain.kernel.pipeline.planning.plan_staleness import (
    capture_entity_versions,
    check_plan_staleness,
    referenced_entity_ids,
)


def _seeded_kg(quantity: int = 5):
    kg = KnowledgeGraph()
    store = onboard_merchant(kg, "m1", "Store", delivery_fee=1.0)["store_id"]
    milk_id = list_product(kg, store, "m1", "Milk", price=3.0, quantity=quantity)["product_id"]
    return kg, milk_id


def _plan_for(product_id: str) -> Plan:
    return Plan(
        goal="buy milk",
        steps=(
            PlanStep(
                action="ProductSelection",
                parameters={"selection": [{"id": product_id, "qty": 1}]},
            ),
            PlanStep(action="OrderCreation", parameters={}),
        ),
    )


def test_referenced_entity_ids_finds_nested_selection_ids():
    kg, milk_id = _seeded_kg()
    plan = _plan_for(milk_id)
    assert referenced_entity_ids(plan) == (milk_id,)


def test_referenced_entity_ids_empty_for_plan_with_no_id_params():
    plan = Plan(
        goal="explain",
        steps=(PlanStep(action="Explain", parameters={"question": "why"}),),
    )
    assert referenced_entity_ids(plan) == ()


def test_unchanged_world_is_not_stale():
    kg, milk_id = _seeded_kg()
    plan = _plan_for(milk_id)
    record = CurrentPlanRecord(
        plan_id="p1",
        actor_id="alice",
        goal="buy milk",
        entity_versions=capture_entity_versions(kg, plan),
    )
    result = check_plan_staleness(kg, record)
    assert result.is_stale is False
    assert result.reasons == ()


def test_depleted_stock_is_stale_with_a_specific_reason():
    kg, milk_id = _seeded_kg(quantity=5)
    plan = _plan_for(milk_id)
    record = CurrentPlanRecord(
        plan_id="p1",
        actor_id="alice",
        goal="buy milk",
        entity_versions=capture_entity_versions(kg, plan),
    )
    assert check_plan_staleness(kg, record).is_stale is False

    kg.update_entity(milk_id, attributes={"quantity": 0})

    result = check_plan_staleness(kg, record)
    assert result.is_stale is True
    assert len(result.reasons) == 1
    reason = result.reasons[0]
    assert reason.entity_id == milk_id
    assert "out of stock" in reason.reason
    assert reason.current_version == 1
    assert reason.recorded_version == 0


def test_deleted_entity_is_stale():
    kg, milk_id = _seeded_kg()
    plan = _plan_for(milk_id)
    versions = capture_entity_versions(kg, plan)
    # Simulate the entity having vanished from a fresh KG (delisted store,
    # or a genuinely different world snapshot) — the referenced id simply
    # doesn't resolve anymore.
    other_kg = KnowledgeGraph()
    record = CurrentPlanRecord(plan_id="p1", actor_id="alice", goal="buy milk", entity_versions=versions)
    result = check_plan_staleness(other_kg, record)
    assert result.is_stale is True
    assert result.reasons[0].reason == "no longer exists"
    assert result.reasons[0].current_version is None


def test_price_change_is_stale():
    kg, milk_id = _seeded_kg()
    plan = _plan_for(milk_id)
    record = CurrentPlanRecord(
        plan_id="p1",
        actor_id="alice",
        goal="buy milk",
        entity_versions=capture_entity_versions(kg, plan),
    )
    kg.update_entity(milk_id, attributes={"price": 999.0})
    result = check_plan_staleness(kg, record)
    assert result.is_stale is True


def test_record_with_no_entity_versions_is_never_stale():
    """A plan that referenced no entities (or a record persisted before
    this field existed) has nothing to compare — absence of evidence is
    not evidence of staleness."""
    kg, _ = _seeded_kg()
    record = CurrentPlanRecord(plan_id="p1", actor_id="alice", goal="explain")
    result = check_plan_staleness(kg, record)
    assert result.is_stale is False


def test_missing_kg_is_never_stale():
    """No knowledge graph in scope (e.g. an autonomous tick with no world
    context) fails open on this specific check — there's nothing to
    revalidate against, and the caller's other gates (last_execution_failed,
    consecutive-skip cap) still apply independently."""
    plan = Plan(
        goal="x",
        steps=(PlanStep(action="A", parameters={"selection": [{"id": "p1"}]}),),
    )
    record = CurrentPlanRecord(plan_id="p1", actor_id="alice", goal="x", entity_versions={"p1": 0})
    result = check_plan_staleness(None, record)
    assert result.is_stale is False


def test_current_plan_record_round_trips_entity_versions():
    kg, milk_id = _seeded_kg()
    plan = _plan_for(milk_id)
    versions = capture_entity_versions(kg, plan)
    record = CurrentPlanRecord(plan_id="p1", actor_id="alice", goal="buy milk", entity_versions=versions)
    restored = CurrentPlanRecord.from_dict(record.to_dict())
    assert restored.entity_versions == versions
