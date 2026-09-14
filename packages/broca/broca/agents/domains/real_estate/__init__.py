"""Real Estate agents — Property, Lease, Tenant, Mortgage, Facility."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.real_estate")


class PropertyAgent(BaseDDDAgent):
    agent_type = "property"
    description = "Manages property listings, valuations, and transactions"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "search"),
            "property_id": context.get("property_id", ""),
            "criteria": context.get("criteria", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"property.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"property.{decision['operation']}", "success": True}


class LeaseAgent(BaseDDDAgent):
    agent_type = "lease"
    description = "Manages lease agreements, renewals, and rent collection"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "query"),
            "lease_id": context.get("lease_id", ""),
            "property_id": context.get("property_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"lease.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"lease.{decision['operation']}", "success": True}


class TenantAgent(BaseDDDAgent):
    agent_type = "tenant"
    description = "Manages tenant relationships, communications, and satisfaction"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "query"),
            "tenant_id": context.get("tenant_id", ""),
            "property_id": context.get("property_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"tenant.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"tenant.{decision['operation']}", "success": True}


class MortgageAgent(BaseDDDAgent):
    agent_type = "mortgage"
    description = "Processes mortgage applications, approvals, and amortization"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "apply"),
            "applicant": context.get("applicant", {}),
            "property_value": context.get("property_value", 0),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"mortgage.{perception['operation']}",
            "approved": True,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"mortgage.{decision['operation']}",
            "success": decision.get("approved", False),
        }


class FacilityAgent(BaseDDDAgent):
    agent_type = "facility"
    description = "Manages facility maintenance, utilities, and operations"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "facility_id": context.get("facility_id", ""),
            "operation": context.get("operation", "status"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"facility.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"facility.{decision['operation']}", "success": True}
