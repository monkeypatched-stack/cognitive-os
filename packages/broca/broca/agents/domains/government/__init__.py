"""Government agents — Citizen, Permit, CaseManagement."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.government")


class CitizenAgent(BaseDDDAgent):
    agent_type = "citizen"
    description = "Manages citizen services, requests, and profiles"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "query"),
            "citizen_id": context.get("citizen_id", ""),
            "request": context.get("request", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"citizen.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"citizen.{decision['operation']}", "success": True}


class PermitAgent(BaseDDDAgent):
    agent_type = "permit"
    description = "Processes permit applications, reviews, and approvals"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "apply"),
            "permit_type": context.get("permit_type", ""),
            "applicant_id": context.get("applicant_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"permit.{perception['operation']}",
            "permit_id": f"prm-{perception.get('applicant_id', '')[:8]}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"permit.{decision['operation']}",
            "success": True,
            "permit_id": decision.get("permit_id", ""),
        }


class CaseManagementAgent(BaseDDDAgent):
    agent_type = "case_management"
    description = "Manages government cases, workflows, and document tracking"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "create"),
            "case_type": context.get("case_type", ""),
            "citizen_id": context.get("citizen_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"case.{perception['operation']}",
            "case_id": f"case-{perception.get('citizen_id', '')[:8]}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"case.{decision['operation']}",
            "success": True,
            "case_id": decision.get("case_id", ""),
        }
