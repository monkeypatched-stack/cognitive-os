"""Telecommunications agents — Network, Fault, Provisioning."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.telecom")


class NetworkAgent(BaseDDDAgent):
    agent_type = "network"
    description = "Monitors network performance, topology, and capacity"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "monitor"),
            "network_id": context.get("network_id", ""),
            "metrics": context.get("metrics", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        utilization = perception.get("metrics", {}).get("utilization", 0)
        return {
            "operation": perception["operation"],
            "action": f"network.{perception['operation']}",
            "health": ("good" if utilization < 70 else "degraded" if utilization < 90 else "critical"),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"network.{decision['operation']}",
            "success": True,
            "health": decision.get("health", "good"),
        }


class FaultAgent(BaseDDDAgent):
    agent_type = "fault"
    description = "Detects, isolates, and resolves network faults"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "detect"),
            "fault_id": context.get("fault_id", ""),
            "severity": context.get("severity", "minor"),
            "affected_service": context.get("affected_service", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"fault.{perception['operation']}",
            "auto_resolvable": perception.get("severity", "minor") == "minor",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"fault.{decision['operation']}",
            "success": True,
            "auto_resolved": decision.get("auto_resolvable", False),
        }


class ProvisioningAgent(BaseDDDAgent):
    agent_type = "provisioning"
    description = "Provisions telecom services and manages service activation"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "activate"),
            "service": context.get("service", ""),
            "customer_id": context.get("customer_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"provisioning.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"provisioning.{decision['operation']}",
            "success": True,
            "activation_id": f"act-{decision.get('customer_id', '')[:8]}",
        }
