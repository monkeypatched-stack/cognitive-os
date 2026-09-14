"""Capability Resolver — selects the implementation for a capability group.

The resolver selects the best implementation:
- task_management → TaskAgent
- content_generation → BlogAgent
- Or: OpenClaw Provider, n8n Workflow, Generated Capability

The resolver is free to choose the best implementation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("agentos.capability_resolver")


@dataclass
class CapabilityResolution:
    """Result of capability resolution."""

    capability: str
    group: str
    implementation: str
    implementation_type: str  # "agent", "provider", "workflow", "generated"
    confidence: float
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "group": self.group,
            "implementation": self.implementation,
            "implementation_type": self.implementation_type,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


# ── Group → Implementation Mapping ────────────────────────────────────────────

GROUP_IMPLEMENTATIONS: dict[str, dict[str, Any]] = {
    "task_management": {
        "primary": "TaskAgent",
        "alternatives": ["n8n_task_workflow", "openclaw_task"],
        "type": "agent",
    },
    "content_generation": {
        "primary": "BlogAgent",
        "alternatives": ["n8n_blog_workflow", "openclaw_blog"],
        "type": "agent",
    },
    "document_processing": {
        "primary": "DocumentAgent",
        "alternatives": ["n8n_document_workflow"],
        "type": "agent",
    },
    "communication": {
        "primary": "EmailAgent",
        "alternatives": ["n8n_email_workflow"],
        "type": "agent",
    },
    "scheduling": {
        "primary": "CalendarAgent",
        "alternatives": ["n8n_calendar_workflow"],
        "type": "agent",
    },
    "travel_booking": {
        "primary": "ReservationAgent",
        "alternatives": ["n8n_hotel_booking_workflow", "openclaw_hospitality"],
        "type": "agent",
    },
    "robotics": {
        "primary": "RobotAgent",
        "alternatives": ["openclaw_robot"],
        "type": "agent",
    },
    "knowledge_management": {
        "primary": "KnowledgeAgent",
        "alternatives": ["n8n_knowledge_workflow"],
        "type": "agent",
    },
}


class CapabilityResolver:
    """Selects the implementation for a capability group.

    The resolver is free to choose the best implementation.
    """

    def __init__(self):
        self._implementations = dict(GROUP_IMPLEMENTATIONS)

    def resolve(self, group: str) -> CapabilityResolution:
        """Resolve a capability group to an implementation."""
        impl_info = self._implementations.get(group, {})

        if not impl_info:
            return CapabilityResolution(
                capability="",
                group=group,
                implementation=f"{group}Agent",
                implementation_type="generated",
                confidence=0.5,
            )

        return CapabilityResolution(
            capability="",
            group=group,
            implementation=impl_info.get("primary", f"{group}Agent"),
            implementation_type=impl_info.get("type", "agent"),
            confidence=0.9,
            metadata={"alternatives": impl_info.get("alternatives", [])},
        )

    def register(self, group: str, implementation: str, impl_type: str = "agent") -> None:
        """Register a new group → implementation mapping."""
        self._implementations[group] = {
            "primary": implementation,
            "alternatives": [],
            "type": impl_type,
        }
