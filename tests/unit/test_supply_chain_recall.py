"""CCB-500 — recall traceability and shortage option preparation."""

from src.monkey_brain.kernel.domains.recall import recall_batch
from src.monkey_brain.kernel.domains.supply_chain import shortage_options
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph


def test_recall_traces_inventory_contacts_households_and_issues_refunds():
    kg = KnowledgeGraph()
    kg.add_entity(
        "store-stock",
        EntityType.ASSET,
        "Store milk stock",
        {
            "batch_id": "MILK-42",
            "quantity": 12,
            "store_id": "costco",
        },
    )
    kg.add_entity(
        "household-purchase",
        EntityType.ASSET,
        "Household milk",
        {
            "batch_id": "MILK-42",
            "quantity": 2,
            "household_id": "household-1",
            "purchase_amount": 8.50,
        },
    )

    result = recall_batch(kg, "MILK-42", "supplier contamination")

    assert result["affected_inventory"] == ("store-stock", "household-purchase")
    assert result["households_contacted"] == ("household-1",)
    assert result["refunds"][0]["amount"] == 8.50
    assert kg.get_entity("store-stock").attributes["quantity"] == 0
    assert kg.get_entity("store-stock").attributes["status"] == "recalled"


def test_shortage_exposes_substitute_alternate_delayed_and_split_options():
    options = shortage_options(
        "milk",
        [
            {"store_id": "walmart", "quantity": 0, "restock_at": "tomorrow"},
            {"store_id": "aldi", "quantity": 2, "split_eligible": True},
        ],
        substitutions=["oat milk", "almond milk"],
    )

    assert options["substitute_brands"] == ("oat milk", "almond milk")
    assert options["alternate_stores"][0]["store_id"] == "aldi"
    assert options["delayed_delivery"][0]["store_id"] == "walmart"
    assert options["split_orders"][0]["store_id"] == "aldi"
