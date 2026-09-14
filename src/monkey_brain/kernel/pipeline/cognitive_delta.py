"""CognitiveDelta — the artifact produced by Commit.

Commit is cognitive — it accepts the new cognitive state.
The delta is then consumed by infrastructure (checkpoint, world update,
analytics, replication, event bus, simulation).

This separates cognitive commitment from side effects.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CognitiveDelta:
    """Structured artifact produced by Commit.

    Captures everything that changed during this reasoning cycle.
    Consumed by infrastructure for checkpoint, world update, replication, etc.
    """

    actor_id: str = ""
    tenant_id: str = ""

    # New knowledge
    new_facts: tuple[dict[str, Any], ...] = ()
    """Facts added during this cycle."""

    updated_beliefs: dict[str, Any] = field(default_factory=dict)
    """Summary of belief state changes (confidence, hypothesis count, etc.)."""

    # Actions taken
    actions: tuple[dict[str, Any], ...] = ()
    """Actions executed during this cycle."""

    # Predictions
    predictions: tuple[dict[str, Any], ...] = ()
    """Predictions made during this cycle."""

    # Learning
    learning_updates: tuple[dict[str, Any], ...] = ()
    """Knowledge acquired during this cycle."""

    # Events for the event bus
    events: tuple[dict[str, Any], ...] = ()
    """Events to publish to the event bus."""

    # Metrics
    metrics: dict[str, Any] = field(default_factory=dict)
    """Cycle-level metrics (duration, stage timings, etc.)."""

    # Metadata
    cycle_number: int = 0
    """Which reasoning cycle this delta represents."""
    timestamp: float = field(default_factory=time.time)
    """When the delta was produced."""
