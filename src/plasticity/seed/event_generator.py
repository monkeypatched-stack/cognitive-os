"""Event Generator — generates realistic runtime events.

Generates:
- Production Events
- Machine Events
- Sensor Events
- Maintenance Events
- Quality Events
- Workflow Events
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class Event:
    """A generated event."""

    event_id: str = field(default_factory=lambda: f"evt-{uuid4().hex[:8]}")
    event_type: str = ""
    entity_type: str = ""
    entity_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "seed_generator"


EVENT_TYPES = {
    "production": [
        "batch_started",
        "batch_completed",
        "batch_failed",
        "step_completed",
        "step_failed",
    ],
    "machine": [
        "machine_started",
        "machine_stopped",
        "machine_fault",
        "machine_maintenance",
    ],
    "sensor": ["reading", "threshold_breach", "anomaly_detected", "calibration_due"],
    "maintenance": ["pm_scheduled", "pm_completed", "cm_created", "cm_completed"],
    "quality": [
        "deviation_created",
        "capa_created",
        "oos_result",
        "inspection_completed",
    ],
    "workflow": [
        "workflow_started",
        "workflow_completed",
        "workflow_failed",
        "capability_invoked",
    ],
}


class EventGenerator:
    """Generates realistic runtime events.

    Generates deterministic events based on entity data.
    """

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self._events: list[Event] = []

    def generate_machine_events(self, machines: list[dict[str, Any]], count: int = 50) -> list[Event]:
        events = []
        for _ in range(min(count, len(machines) * 3)):
            machine = self._rng.choice(machines)
            event_type = self._rng.choice(EVENT_TYPES["machine"])
            event = Event(
                event_type=event_type,
                entity_type="machine",
                entity_id=machine["id"],
                data={
                    "machine_name": machine.get("name", ""),
                    "status": machine.get("status", ""),
                    "reading": self._rng.uniform(20, 100),
                },
            )
            events.append(event)
            self._events.append(event)
        return events

    def generate_batch_events(self, batches: list[dict[str, Any]], count: int = 30) -> list[Event]:
        events = []
        for _ in range(min(count, len(batches) * 2)):
            batch = self._rng.choice(batches)
            event_type = self._rng.choice(EVENT_TYPES["production"])
            event = Event(
                event_type=event_type,
                entity_type="batch",
                entity_id=batch["id"],
                data={
                    "batch_number": batch.get("batch_number", ""),
                    "status": batch.get("status", ""),
                },
            )
            events.append(event)
            self._events.append(event)
        return events

    def generate_sensor_events(self, machines: list[dict[str, Any]], count: int = 100) -> list[Event]:
        events = []
        for _ in range(min(count, len(machines) * 5)):
            machine = self._rng.choice(machines)
            event_type = self._rng.choice(EVENT_TYPES["sensor"])
            event = Event(
                event_type=event_type,
                entity_type="sensor",
                entity_id=f"{machine['id']}-SENSOR",
                data={
                    "machine_id": machine["id"],
                    "metric": self._rng.choice(["temperature", "pressure", "vibration", "humidity"]),
                    "value": self._rng.uniform(0, 100),
                    "unit": self._rng.choice(["°C", "bar", "mm/s", "%"]),
                },
            )
            events.append(event)
            self._events.append(event)
        return events

    def generate_all_events(self, hierarchy: dict[str, Any]) -> list[Event]:
        events = []
        events.extend(self.generate_machine_events(hierarchy.get("machines", []), 50))
        events.extend(self.generate_batch_events(hierarchy.get("batches", []), 30))
        events.extend(self.generate_sensor_events(hierarchy.get("machines", []), 100))
        return events

    def summary(self) -> dict:
        by_type = {}
        for e in self._events:
            by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
        return {"total": len(self._events), "by_type": by_type}
