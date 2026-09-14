"""Capability Router — maps canonical capabilities to capability groups.

The router maps canonical capabilities to runtime capability groups.
The router should never know about agents.

Examples:
- task.create → task_management
- blog.write → content_generation
- document.summarize → document_processing
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("agentos.capability_router")


@dataclass
class CapabilityRoute:
    """A route from capability to capability group."""

    capability: str
    group: str
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


# ── Capability → Group Mapping ────────────────────────────────────────────────

CAPABILITY_GROUPS: dict[str, str] = {
    # task.* → task_management
    "task.create": "task_management",
    "task.update": "task_management",
    "task.delete": "task_management",
    "task.list": "task_management",
    # blog.* → content_generation
    "blog.write": "content_generation",
    "blog.edit": "content_generation",
    # document.* → document_processing
    "document.summarize": "document_processing",
    "document.translate": "document_processing",
    # email.* → communication
    "email.send": "communication",
    # calendar.* → scheduling
    "calendar.schedule": "scheduling",
    # travel.* → travel_booking
    "travel.hotel_book": "travel_booking",
    "travel.hotel_search": "travel_booking",
    # robot.* → robotics
    "robot.move": "robotics",
    "robot.inspect": "robotics",
    # knowledge.* → knowledge_management
    "knowledge.search": "knowledge_management",
}


class CapabilityRouter:
    """Maps canonical capabilities to capability groups.

    The router should never know about agents.
    """

    def __init__(self):
        self._groups = dict(CAPABILITY_GROUPS)

    def route(self, capability: str) -> CapabilityRoute:
        """Route a capability to its group."""
        group = self._groups.get(capability, "unknown")

        return CapabilityRoute(
            capability=capability,
            group=group,
            metadata={"source": "router"},
        )

    def register(self, capability: str, group: str) -> None:
        """Register a new capability → group mapping."""
        self._groups[capability] = group

    def list_capabilities(self) -> list[str]:
        """List all registered capabilities."""
        return list(self._groups.keys())

    def list_groups(self) -> list[str]:
        """List all unique capability groups."""
        return list(set(self._groups.values()))
