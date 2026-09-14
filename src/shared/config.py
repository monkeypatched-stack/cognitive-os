"""Shared Configuration — constants used across kernel and actor boundaries.

These constants were previously in actor/config.py but are needed by both
kernel and actor. Moving them here breaks the circular import.
"""

from __future__ import annotations

from typing import Any

# Actor configuration constants
ACTOR_CONFIG: dict[str, Any] = {
    "max_ticks": 100,
    "tick_timeout": 30.0,
    "belief_update_threshold": 0.1,
    "gossip_enabled": True,
    "gossip_interval": 10,
}

# Domain hints for intent classification
DOMAIN_HINTS: dict[str, list[str]] = {
    "manufacturing": ["production", "assembly", "quality", "maintenance"],
    "logistics": ["delivery", "shipping", "warehouse", "inventory"],
    "finance": ["payment", "invoice", "budget", "cost"],
    "hr": ["employee", "hiring", "training", "performance"],
}

# Domain keywords for question classification
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "manufacturing": ["line", "machine", "equipment", "calibration", "due"],
    "logistics": ["route", "delivery", "truck", "warehouse"],
    "finance": ["invoice", "payment", "budget", "cost"],
    "hr": ["employee", "hire", "train", "performance"],
}

# Default constraints for planning
DEFAULT_CONSTRAINTS: list[dict[str, Any]] = [
    {"type": "time", "max_duration": 3600},
    {"type": "cost", "max_budget": 1000},
    {"type": "resource", "max_concurrent": 5},
]

# Generic fallback transitions
GENERIC_FALLBACK_TRANSITIONS: dict[str, dict[str, str]] = {
    "idle": {"next": "planning"},
    "planning": {"next": "executing"},
    "executing": {"next": "complete"},
}

# Planning configuration
PLANNING_CONFIG: dict[str, Any] = {
    "max_steps": 10,
    "timeout_seconds": 30,
    "retry_count": 2,
}

# Entity to state mapping
ENTITY_TO_STATE: dict[str, str] = {
    "person": "active",
    "robot": "operational",
    "machine": "running",
    "vehicle": "available",
}

# Domain start states
DOMAIN_START_STATES: dict[str, str] = {
    "manufacturing": "idle",
    "logistics": "pending",
    "finance": "draft",
    "hr": "initiated",
}
