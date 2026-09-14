"""Healthcare agents — Patient, Appointment, Pharmacy, Laboratory, ClinicalWorkflow."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.healthcare")


class PatientAgent(BaseDDDAgent):
    agent_type = "patient"
    description = "Manages patient records, demographics, and medical history"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "find"),
            "patient_id": context.get("patient_id", ""),
            "query": context.get("query", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"patient.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"patient.{decision['operation']}", "success": True}


class AppointmentAgent(BaseDDDAgent):
    agent_type = "appointment"
    description = "Schedules and manages medical appointments"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "book"),
            "patient_id": context.get("patient_id", ""),
            "doctor_id": context.get("doctor_id", ""),
            "slot": context.get("slot", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"appointment.{perception['operation']}",
            "available": True,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"appointment.{decision['operation']}",
            "success": decision.get("available", False),
            "appointment_id": f"appt-{decision.get('patient_id', '')[:8]}",
        }


class PharmacyAgent(BaseDDDAgent):
    agent_type = "pharmacy"
    description = "Manages pharmacy inventory, dispensing, and drug interactions"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "dispense"),
            "medication": context.get("medication", ""),
            "patient_id": context.get("patient_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"pharmacy.{perception['operation']}",
            "safe": True,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"pharmacy.{decision['operation']}",
            "success": decision.get("safe", True),
        }


class LaboratoryAgent(BaseDDDAgent):
    agent_type = "laboratory"
    description = "Manages lab orders, results, and reference ranges"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "order"),
            "test_type": context.get("test_type", ""),
            "patient_id": context.get("patient_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"laboratory.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"laboratory.{decision['operation']}",
            "success": True,
            "order_id": f"lab-{decision.get('test_type', 'test')[:8]}",
        }


class ClinicalWorkflowAgent(BaseDDDAgent):
    agent_type = "clinical_workflow"
    description = "Orchestrates clinical workflows and care pathways"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "patient_id": context.get("patient_id", ""),
            "workflow": context.get("workflow", ""),
            "step": context.get("step", 0),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "clinical.advance",
            "next_step": perception.get("step", 0) + 1,
            "complete": False,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "clinical.advance",
            "success": True,
            "next_step": decision.get("next_step", 0),
        }
