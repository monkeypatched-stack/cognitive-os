"""Hospitality agents — Reservation, Guest, Housekeeping, Concierge, Room, Event."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.hospitality")


class ReservationAgent(BaseDDDAgent):
    agent_type = "reservation"
    description = "Manages hotel reservations, availability, and bookings"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "book"),
            "check_in": context.get("check_in", ""),
            "check_out": context.get("check_out", ""),
            "room_type": context.get("room_type", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"reservation.{perception['operation']}",
            "available": True,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"reservation.{decision['operation']}",
            "success": decision.get("available", False),
            "confirmation": f"res-{decision.get('check_in', '')[:8]}",
        }


class GuestAgent(BaseDDDAgent):
    agent_type = "guest"
    description = "Manages guest profiles, preferences, and loyalty"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "query"),
            "guest_id": context.get("guest_id", ""),
            "stay_id": context.get("stay_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"guest.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"guest.{decision['operation']}", "success": True}


class HousekeepingAgent(BaseDDDAgent):
    agent_type = "housekeeping"
    description = "Manages room cleaning schedules and status tracking"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "room_id": context.get("room_id", ""),
            "operation": context.get("operation", "status"),
            "priority": context.get("priority", "normal"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"housekeeping.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"housekeeping.{decision['operation']}", "success": True}


class ConciergeAgent(BaseDDDAgent):
    agent_type = "concierge"
    description = "Handles guest requests, recommendations, and special arrangements"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "guest_id": context.get("guest_id", ""),
            "request": context.get("request", ""),
            "priority": context.get("priority", "normal"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {"action": "concierge.fulfill", "fulfilled": True}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "concierge.fulfill",
            "success": decision.get("fulfilled", False),
        }


class RoomAgent(BaseDDDAgent):
    agent_type = "room"
    description = "Manages room status, pricing, and assignment"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "room_id": context.get("room_id", ""),
            "operation": context.get("operation", "status"),
            "room_type": context.get("room_type", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"room.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"room.{decision['operation']}", "success": True}


class HospitalityEventAgent(BaseDDDAgent):
    agent_type = "hospitality_event"
    description = "Manages event bookings, banquets, and conference logistics"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "book"),
            "event": context.get("event", {}),
            "venue": context.get("venue", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"hospitality_event.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"hospitality_event.{decision['operation']}", "success": True}
