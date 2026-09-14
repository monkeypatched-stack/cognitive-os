"""EventBus — publish/subscribe event bus for the Cognitive Platform.

Decouples the Global World Model from Runtime Services and Actors.
All communication between components flows through the Event Bus.

Events:
    world.updated       — Global World was modified by Context Stream
    belief.updated      — Actor Belief was updated by observation
    bellman.updated     — Bellman policy was updated
    phi.compiled        — Sparse operator Φ was recompiled
    plan.ready          — Planner produced a new plan
    prediction.ready    — Prediction completed on cloned world
    actor.acted         — Actor completed an action
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("agentos.event_bus")


@dataclass
class Event:
    """An event published through the bus."""

    topic: str
    payload: dict
    timestamp: float = field(default_factory=time.time)
    source: str = ""


class EventBus:
    """Publish/subscribe event bus for the Cognitive Platform.

    All inter-component communication flows through the Event Bus.
    Components never call each other directly.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[Event], None]]] = defaultdict(list)
        self._wildcard_subscribers: list[Callable[[Event], None]] = []
        self._event_log: list[Event] = []
        self._max_log = 10000

    def publish(self, topic: str, payload: dict, source: str = "") -> Event:
        """Publish an event to the bus."""
        event = Event(topic=topic, payload=payload, source=source)
        self._event_log.append(event)
        if len(self._event_log) > self._max_log:
            self._event_log = self._event_log[-self._max_log :]

        failed_subscribers = []

        for sub in self._subscribers.get(topic, []):
            try:
                sub(event)
            except Exception as e:
                logger.error("[event_bus] subscriber error on %s: %s", topic, e)
                failed_subscribers.append((topic, sub))

        for sub in self._wildcard_subscribers:
            try:
                sub(event)
            except Exception as e:
                logger.error("[event_bus] wildcard subscriber error: %s", e)

        # Remove failing subscribers
        for t, sub in failed_subscribers:
            self._subscribers[t] = [s for s in self._subscribers[t] if s is not sub]

        return event

    def subscribe_pattern(self, pattern: str, callback: Callable[[Event], None]) -> None:
        """Subscribe to events matching a topic pattern (supports * wildcard)."""
        import fnmatch

        def pattern_matcher(event: Event) -> None:
            if fnmatch.fnmatch(event.topic, pattern):
                callback(event)

        self._wildcard_subscribers.append(pattern_matcher)

    def subscribe(self, topic: str, callback: Callable[[Event], None]) -> None:
        self._subscribers[topic].append(callback)

    def subscribe_all(self, callback: Callable[[Event], None]) -> None:
        self._wildcard_subscribers.append(callback)

    def unsubscribe(self, topic: str, callback: Callable[[Event], None]) -> None:
        self._subscribers[topic] = [s for s in self._subscribers[topic] if s is not callback]

    def recent(self, topic: str | None = None, limit: int = 10) -> list[Event]:
        events = self._event_log if topic is None else [e for e in self._event_log if e.topic == topic]
        return events[-limit:]

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Save event log to file."""
        import json
        import os

        os.makedirs(path, exist_ok=True)
        data = [
            {
                "topic": e.topic,
                "payload": e.payload,
                "timestamp": e.timestamp,
                "source": e.source,
            }
            for e in self._event_log
        ]
        with open(os.path.join(path, "event_log.json"), "w") as f:
            json.dump(data, f, indent=2)

    def load(self, path: str) -> None:
        """Load event log from file."""
        import json
        import os

        log_path = os.path.join(path, "event_log.json")
        if not os.path.exists(log_path):
            return
        with open(log_path) as f:
            data = json.load(f)
        self._event_log = [
            Event(
                topic=d["topic"],
                payload=d["payload"],
                timestamp=d["timestamp"],
                source=d["source"],
            )
            for d in data
        ]
