"""CCB-400 — cross-store learning and dynamic reputation."""

from src.monkey_brain.kernel.pipeline.belief_state import Goal
from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
from src.monkey_brain.kernel.pipeline.planning.context_engine import (
    ContextConstructionEngine,
)
from src.monkey_brain.kernel.society.commerce_network import (
    CommerceExperience,
    CommerceNetwork,
)
from src.monkey_brain.kernel.society.commerce_network import CapabilityPublication
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def test_supplier_experience_is_published_and_available_to_network_planning():
    network = CommerceNetwork()
    network.publish_experience(
        CommerceExperience(
            source_store_id="store-a",
            subject="milk supplier",
            lesson="Supplier X reduces spoilage",
            confidence=0.95,
        )
    )
    facts = network.planning_facts("milk")

    assert any(f["lesson"] == "Supplier X reduces spoilage" for f in facts)


def test_repeated_late_delivery_lowers_store_reputation():
    network = CommerceNetwork()
    initial = network.reputation("walmart")
    for _ in range(3):
        network.record_delivery("walmart", late=True)

    current = network.reputation("walmart")
    assert current.score < initial.score
    assert current.late_deliveries == 3
    assert any(e.source_store_id == "walmart" for e in network.experiences("delivery"))


def test_network_facts_reach_llm_planning_context():
    pr = PlanetaryRuntime()
    network = pr.commerce_network
    network.publish_experience(
        CommerceExperience(
            source_store_id="costco",
            subject="milk supplier",
            lesson="Supplier X reduces spoilage",
            confidence=0.9,
        )
    )
    network.record_delivery("walmart", late=True)
    engine = ContextConstructionEngine(planetary_runtime=pr)
    context = engine.build("unknown", Goal(name="buy milk"))
    prompt = LLMPlanner(backend=object())._build_prompt(context.goal, [], context)

    assert "Supplier X reduces spoilage" in prompt
    assert "walmart" in prompt


def test_household_and_store_preferences_are_learned_without_reasking():
    network = CommerceNetwork()
    for _ in range(3):
        network.learn_preference("household-1", "milk", "organic")
    network.learn_preference("store-a", "brand-rejection", "Brand X")

    assert network.preference("household-1", "milk") == "organic"
    assert network.preference("store-a", "brand-rejection") == "Brand X"


def test_store_capability_is_published_and_adopted():
    network = CommerceNetwork()
    network.publish_capability(
        CapabilityPublication(
            publisher_store_id="costco",
            name="holiday_inventory_optimization",
            description="Optimize holiday inventory before peak demand.",
        )
    )

    adoption = network.adopt_capability("aldi", "holiday_inventory_optimization")
    assert adoption["publisher_store_id"] == "costco"
    assert adoption["store_id"] == "aldi"
