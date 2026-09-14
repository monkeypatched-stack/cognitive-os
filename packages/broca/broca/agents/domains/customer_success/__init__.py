"""Customer Success agents — CustomerSuccess, Ticket, Escalation, SLA, Feedback, Survey."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.customer_success")


class CustomerSuccessAgent(BaseDDDAgent):
    agent_type = "customer_success"
    description = "Manages customer health scores, adoption, and retention"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "customer_id": context.get("customer_id", ""),
            "health_score": context.get("health_score", 0),
            "usage": context.get("usage", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        health = perception.get("health_score", 0)
        return {
            "action": "customer_success.assess",
            "risk_level": ("low" if health > 0.7 else "medium" if health > 0.4 else "high"),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "customer_success.assess",
            "risk_level": decision.get("risk_level", "low"),
            "recommendations": [],
        }


class TicketAgent(BaseDDDAgent):
    agent_type = "ticket"
    description = "Manages support tickets, routing, and resolution tracking"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "create"),
            "ticket": context.get("ticket", {}),
            "priority": context.get("priority", "medium"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"ticket.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"ticket.{decision['operation']}",
            "success": True,
            "ticket_id": f"tkt-{decision.get('priority', 'med')[:3]}",
        }


class EscalationAgent(BaseDDDAgent):
    agent_type = "escalation"
    description = "Manages escalation paths and SLA breach responses"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "ticket_id": context.get("ticket_id", ""),
            "severity": context.get("severity", "medium"),
            "sla_breach": context.get("sla_breach", False),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        needs_escalation = perception.get("sla_breach", False) or perception.get("severity", "medium") == "critical"
        return {
            "action": "escalation.evaluate",
            "escalate": needs_escalation,
            "level": "management" if needs_escalation else "none",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "escalation.evaluate",
            "escalated": decision.get("escalate", False),
            "level": decision.get("level", "none"),
        }


class SLAAgent(BaseDDDAgent):
    agent_type = "sla"
    description = "Tracks SLA compliance, response times, and resolution targets"
    ddd_layer = "value_object"
    workload_spec = "source_control"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "ticket_id": context.get("ticket_id", ""),
            "sla_target": context.get("sla_target", {}),
            "actual_time": context.get("actual_time", 0),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        target = perception.get("sla_target", {}).get("response_hours", 24)
        actual = perception.get("actual_time", 0)
        return {
            "action": "sla.check",
            "met": actual <= target,
            "breach_hours": max(0, actual - target),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "sla.check",
            "met": decision.get("met", True),
            "breach_hours": decision.get("breach_hours", 0),
        }


class FeedbackAgent(BaseDDDAgent):
    agent_type = "feedback"
    description = "Collects, categorizes, and analyzes customer feedback"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "customer_id": context.get("customer_id", ""),
            "feedback": context.get("feedback", ""),
            "rating": context.get("rating", 0),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "feedback.categorize",
            "category": "general",
            "sentiment": "neutral",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "feedback.categorize",
            "success": True,
            "category": decision.get("category", "general"),
        }


class SurveyAgent(BaseDDDAgent):
    agent_type = "survey"
    description = "Creates, distributes, and analyzes surveys"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "create"),
            "survey": context.get("survey", {}),
            "audience": context.get("audience", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"survey.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"survey.{decision['Operation']}", "success": True}
