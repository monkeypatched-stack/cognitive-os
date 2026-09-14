"""Pharmaceutical agents — ClinicalTrial, DrugDiscovery, RegulatorySubmission, Pharmacovigilance."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.pharma")


class ClinicalTrialAgent(BaseDDDAgent):
    agent_type = "clinical_trial"
    description = "Manages clinical trial design, enrollment, and monitoring"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "trial_id": context.get("trial_id", ""),
            "operation": context.get("operation", "status"),
            "phase": context.get("phase", "I"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"clinical_trial.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"clinical_trial.{decision['operation']}", "success": True}


class DrugDiscoveryAgent(BaseDDDAgent):
    agent_type = "drug_discovery"
    description = "Manages drug discovery pipeline, target identification, and lead optimization"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "pipeline": context.get("pipeline", ""),
            "operation": context.get("operation", "query"),
            "target": context.get("target", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"drug_discovery.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"drug_discovery.{decision['operation']}", "success": True}


class RegulatorySubmissionAgent(BaseDDDAgent):
    agent_type = "regulatory_submission"
    description = "Manages FDA/regulatory submissions and approval tracking"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "submission_id": context.get("submission_id", ""),
            "operation": context.get("operation", "status"),
            "agency": context.get("agency", "FDA"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"regulatory_submission.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"regulatory_submission.{decision['Operation']}",
            "success": True,
        }


class PharmacovigilanceAgent(BaseDDDAgent):
    agent_type = "pharmacovigilance"
    description = "Monitors drug safety, adverse events, and risk management"
    ddd_layer = "policy"
    workload_spec = "source_control"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "drug_id": context.get("drug_id", ""),
            "events": context.get("events", []),
            "operation": context.get("operation", "monitor"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        serious = [e for e in perception.get("events", []) if e.get("serious", False)]
        return {
            "operation": perception["operation"],
            "action": f"pharmacovigilance.{perception['operation']}",
            "serious_events": len(serious),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"pharmacovigilance.{decision['Operation']}",
            "success": True,
            "serious_events": decision.get("serious_events", 0),
        }
