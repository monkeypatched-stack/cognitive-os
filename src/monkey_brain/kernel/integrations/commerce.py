"""Side-effecting adapters for commerce capabilities.

Adapters execute commands selected by the planner. They do not select
products, stores, suppliers, quantities, or refund policy.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4


class SharedResourceInventoryIntegration:
    def __init__(self, resource_reader, resource_writer, event_writer=None):
        self._read = resource_reader
        self._write = resource_writer
        self._event = event_writer or (lambda _description: None)

    def observe(self, item: str) -> dict[str, Any]:
        resources = self._read()
        inventory = resources.get("inventory", {})
        record = dict(inventory.get(item, {}))
        quantity = float(record.get("quantity", 0))
        minimum = float(record.get("minimum", 0))
        return {
            "item": item,
            "quantity": quantity,
            "minimum": minimum,
            "needs_replenishment": quantity <= minimum,
            "warehouse": resources.get("warehouse", {}),
            "supplier": resources.get("supplier", {}),
        }

    def apply_replenishment(self, item: str, quantity: float, supplier: str = "") -> dict[str, Any]:
        resources = self._read()
        inventory = dict(resources.get("inventory", {}))
        record = dict(inventory.get(item, {}))
        record["quantity"] = float(record.get("quantity", 0)) + float(quantity)
        if supplier:
            record["last_supplier"] = supplier
        inventory[item] = record
        self._write(inventory=inventory)
        self._event(f"replenished {item} by {quantity} from {supplier or 'supplier'}")
        return {"item": item, **record}


class KnowledgeGraphRecallIntegration:
    def __init__(self, kg: Any):
        self._kg = kg

    def execute(self, batch_id: str, reason: str) -> dict[str, Any]:
        affected_inventory, households, refunds, notifications = [], set(), [], []
        for entity in list(self._kg.entities):
            if entity.attributes.get("batch_id") != batch_id:
                continue
            attrs = {
                "status": "recalled",
                "recall_reason": reason,
                "recalled_at": time.time(),
            }
            if entity.attributes.get("quantity") is not None:
                attrs["quantity"] = 0
                affected_inventory.append(entity.entity_id)
            self._kg.update_entity(entity.entity_id, attributes=attrs)
            household_id = entity.attributes.get("household_id") or entity.attributes.get("owner_id")
            if household_id:
                households.add(household_id)
                amount = entity.attributes.get("purchase_amount", entity.attributes.get("price", 0))
                refunds.append(
                    {
                        "refund_id": uuid4().hex,
                        "household_id": household_id,
                        "batch_id": batch_id,
                        "amount": amount,
                        "status": "issued",
                    }
                )
                notifications.append(
                    {
                        "recipient_id": household_id,
                        "batch_id": batch_id,
                        "message": f"Recall for batch {batch_id}: remove product and refund issued.",
                    }
                )
        return {
            "batch_id": batch_id,
            "reason": reason,
            "affected_inventory": tuple(affected_inventory),
            "households_contacted": tuple(sorted(households)),
            "notifications": tuple(notifications),
            "refunds": tuple(refunds),
        }
