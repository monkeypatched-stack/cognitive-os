"""Energy agents — Grid, Utility, Asset, Outage."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.energy")


class GridAgent(BaseDDDAgent):
    agent_type = "grid"
    description = "Monitors grid state, load balancing, and power flow"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "monitor"),
            "grid_id": context.get("grid_id", ""),
            "metrics": context.get("metrics", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        load = perception.get("metrics", {}).get("load_percent", 0)
        return {
            "operation": perception["operation"],
            "action": f"grid.{perception['operation']}",
            "load_status": ("normal" if load < 80 else "high" if load < 95 else "critical"),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"grid.{decision['operation']}",
            "success": True,
            "load_status": decision.get("load_status", "normal"),
        }


class UtilityAgent(BaseDDDAgent):
    agent_type = "utility"
    description = "Manages utility billing, metering, and customer accounts"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "read_meter"),
            "account_id": context.get("account_id", ""),
            "meter_id": context.get("meter_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"utility.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"utility.{decision['operation']}", "success": True}


class EnergyAssetAgent(BaseDDDAgent):
    agent_type = "energy_asset"
    description = "Tracks energy assets (transformers, substations, generators)"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "asset_id": context.get("asset_id", ""),
            "operation": context.get("operation", "status"),
            "asset_type": context.get("asset_type", "transformer"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"energy_asset.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"energy_asset.{decision['operation']}", "success": True}


class OutageAgent(BaseDDDAgent):
    agent_type = "outage"
    description = "Detects, tracks, and manages power outages and restoration"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "report"),
            "outage_id": context.get("outage_id", ""),
            "affected_area": context.get("affected_area", ""),
            "cause": context.get("cause", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"outage.{perception['operation']}",
            "severity": ("high" if perception.get("cause") == "equipment_failure" else "medium"),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"outage.{decision['operation']}",
            "success": True,
            "outage_id": decision.get("outage_id", ""),
            "severity": decision.get("severity", "medium"),
        }
