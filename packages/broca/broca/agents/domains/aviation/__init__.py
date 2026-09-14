"""Aviation agents — Flight, Crew, Aircraft."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.aviation")


class FlightAgent(BaseDDDAgent):
    agent_type = "flight"
    description = "Manages flight operations, scheduling, and tracking"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "schedule"),
            "flight_id": context.get("flight_id", ""),
            "route": context.get("route", {}),
            "aircraft_id": context.get("aircraft_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"flight.{perception['operation']}",
            "cleared": True,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"flight.{decision['operation']}",
            "success": decision.get("cleared", False),
            "flight_id": decision.get("flight_id", ""),
        }


class CrewAgent(BaseDDDAgent):
    agent_type = "crew"
    description = "Manages crew scheduling, certifications, and duty hours"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "schedule"),
            "crew_id": context.get("crew_id", ""),
            "flight_id": context.get("flight_id", ""),
            "role": context.get("role", "pilot"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"crew.{perception['operation']}",
            "available": True,
            "certified": True,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"crew.{decision['operation']}",
            "success": decision.get("available", False) and decision.get("certified", False),
        }


class AircraftAgent(BaseDDDAgent):
    agent_type = "aircraft"
    description = "Tracks aircraft status, maintenance, and airworthiness"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "aircraft_id": context.get("aircraft_id", ""),
            "operation": context.get("operation", "status"),
            "maintenance_due": context.get("maintenance_due", False),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"aircraft.{perception['operation']}",
            "airworthy": not perception.get("maintenance_due", False),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"aircraft.{decision['operation']}",
            "success": True,
            "airworthy": decision.get("airworthy", True),
        }
