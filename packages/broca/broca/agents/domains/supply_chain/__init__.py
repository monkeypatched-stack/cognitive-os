"""Supply Chain agents — Procurement, PurchaseOrder, Logistics, Distribution."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.supply_chain")


class ProcurementAgent(BaseDDDAgent):
    agent_type = "procurement"
    description = "Manages procurement processes, sourcing, and vendor selection"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "source"),
            "items": context.get("items", []),
            "budget": context.get("budget", 0),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"procurement.{perception['operation']}",
            "within_budget": sum(i.get("cost", 0) for i in perception.get("items", []))
            <= perception.get("budget", float("inf")),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"procurement.{decision['operation']}",
            "success": decision.get("within_budget", True),
        }


class PurchaseOrderAgent(BaseDDDAgent):
    agent_type = "purchase_order"
    description = "Creates and manages purchase orders"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "create"),
            "po": context.get("po", {}),
            "supplier_id": context.get("supplier_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"purchase_order.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"purchase_order.{decision['operation']}",
            "success": True,
            "po_id": f"po-{decision.get('supplier_id', '')[:8]}",
        }


class LogisticsAgent(BaseDDDAgent):
    agent_type = "logistics"
    description = "Manages transportation, routing, and shipment tracking"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "route"),
            "origin": context.get("origin", ""),
            "destination": context.get("destination", ""),
            "cargo": context.get("cargo", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"logistics.{perception['operation']}",
            "route_optimal": True,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"logistics.{decision['operation']}",
            "success": True,
            "tracking_id": f"log-{decision.get('origin', '')[:4]}",
        }


class DistributionAgent(BaseDDDAgent):
    agent_type = "distribution"
    description = "Manages distribution centers, allocation, and delivery schedules"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "allocate"),
            "center_id": context.get("center_id", ""),
            "orders": context.get("orders", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"distribution.{perception['operation']}",
            "orders_count": len(perception.get("orders", [])),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"distribution.{decision['operation']}",
            "success": True,
            "allocated": decision.get("orders_count", 0),
        }
