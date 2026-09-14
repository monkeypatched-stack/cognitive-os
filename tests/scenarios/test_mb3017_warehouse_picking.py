"""MB-3017 Warehouse Picking — picker assignment scenario.

Assign picker.

Built kernel/domains/logistics.py::assign_picker() for this — it mirrors
the design already established by select_delivery_riders() (the delivery
side's "assign a SPECIFIC rider, not just a generic label" mechanism):
the store's own autonomous cart if it has one (never "unavailable" the
way a specific employee can be), otherwise the fastest AVAILABLE human
picker actually assigned to that store. estimate_pickup_minutes() already
existed but only ever returned a generic time + label ("human picker"/
"autonomous cart") — never a specific, identifiable picker.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.logistics import LogisticsCapability, assign_picker
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

ROBOT_STORE = "store_robot"
HUMAN_STORE = "store_human"


def _seed_world() -> KnowledgeGraph:
    kg = KnowledgeGraph()
    kg.add_entity(
        ROBOT_STORE,
        EntityType.ORGANIZATION,
        "Robo Mart",
        {
            "has_autonomous_cart": True,
            "robot_pick_minutes": 4.0,
        },
    )
    kg.add_entity(HUMAN_STORE, EntityType.ORGANIZATION, "Human Mart", {})
    kg.add_entity(
        "picker_slow",
        EntityType.PERSON,
        "Sam",
        {
            "role": "picker",
            "store_id": HUMAN_STORE,
            "status": "available",
            "pick_rate_minutes": 20.0,
        },
    )
    kg.add_entity(
        "picker_fast",
        EntityType.PERSON,
        "Rae",
        {
            "role": "picker",
            "store_id": HUMAN_STORE,
            "status": "available",
            "pick_rate_minutes": 8.0,
        },
    )
    kg.add_entity(
        "picker_busy",
        EntityType.PERSON,
        "Jo",
        {
            "role": "picker",
            "store_id": HUMAN_STORE,
            "status": "busy",
            "pick_rate_minutes": 5.0,
        },
    )
    return kg


def test_mb3017_store_with_autonomous_cart_assigns_the_cart():
    kg = _seed_world()

    result = assign_picker(kg, ROBOT_STORE)

    assert result["success"] is True
    assert result["picker_type"] == "autonomous_cart"
    assert result["estimated_minutes"] == 4.0


def test_mb3017_human_store_assigns_fastest_available_picker():
    kg = _seed_world()

    result = assign_picker(kg, HUMAN_STORE)

    assert result["success"] is True
    assert result["picker_type"] == "human"
    assert result["picker_id"] == "picker_fast"
    assert result["picker_name"] == "Rae"
    # The busy, faster picker (5.0 min) must never be chosen over an
    # available, slower one.
    assert result["estimated_minutes"] == 8.0


def test_mb3017_picker_at_a_different_store_is_never_assigned():
    kg = _seed_world()
    kg.add_entity(
        "picker_elsewhere",
        EntityType.PERSON,
        "Kim",
        {
            "role": "picker",
            "store_id": ROBOT_STORE,
            "status": "available",
            "pick_rate_minutes": 1.0,
        },
    )

    result = assign_picker(kg, HUMAN_STORE)

    assert result["picker_id"] != "picker_elsewhere"


def test_mb3017_a_rider_is_never_assigned_as_a_picker():
    kg = _seed_world()
    kg.add_entity(
        "rider_1",
        EntityType.PERSON,
        "Ravi",
        {
            "role": "rider",
            "store_id": HUMAN_STORE,
            "status": "available",
            "pick_rate_minutes": 0.5,
        },
    )

    result = assign_picker(kg, HUMAN_STORE)

    assert result["picker_id"] != "rider_1"
    assert result["picker_id"] == "picker_fast"


def test_mb3017_no_eligible_picker_is_an_honest_failure():
    kg = _seed_world()
    kg.add_entity("store_empty", EntityType.ORGANIZATION, "Empty Mart", {})

    result = assign_picker(kg, "store_empty")

    assert result["success"] is False
    assert "no available picker" in result["error"]


def test_mb3017_unknown_store_is_an_honest_failure():
    kg = _seed_world()

    result = assign_picker(kg, "does-not-exist")

    assert result["success"] is False
    assert "not found" in result["error"]


def test_mb3017_assign_picker_via_capability():
    kg = _seed_world()
    cap = LogisticsCapability()

    assert cap.can_handle("assign_picker")
    assert cap.invoke("assign_picker", kg, HUMAN_STORE) == assign_picker(kg, HUMAN_STORE)
