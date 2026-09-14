"""Construction agents — Construction, Site, EquipmentScheduling."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.construction")


class ConstructionAgent(BaseDDDAgent):
    agent_type = "construction"
    description = "Manages construction projects, timelines, and budgets"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": context.get("project_id", ""),
            "operation": context.get("operation", "status"),
            "phase": context.get("phase", "planning"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"construction.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"construction.{decision['operation']}", "success": True}


class SiteAgent(BaseDDDAgent):
    agent_type = "site"
    description = "Manages construction site operations, safety, and inspections"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "site_id": context.get("site_id", ""),
            "operation": context.get("operation", "inspect"),
            "checklist": context.get("checklist", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"site.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"site.{decision['operation']}", "success": True}


class EquipmentSchedulingAgent(BaseDDDAgent):
    agent_type = "equipment_scheduling"
    description = "Schedules construction equipment and prevents conflicts"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "equipment": context.get("equipment", []),
            "tasks": context.get("tasks", []),
            "site_id": context.get("site_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "equipment_scheduling.optimize",
            "equipment_count": len(perception.get("equipment", [])),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "equipment_scheduling.optimize",
            "success": True,
            "schedule": {},
        }
