"""EventAgent — captures domain events, detects patterns, drives temporal causality."""

from __future__ import annotations
import logging
import time
from typing import Any, Callable
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.ddd.event")


class EventAgent(BaseDDDAgent):
    """Event agent — owns temporal causality.

    Perceive: reads incoming domain event from context.
    Reason:   correlates with event log; detects registered patterns.
    Act:      records the event, fires pattern handlers, emits derived events.
    Learn:    tracks event frequency to detect anomalies and optimise patterns.
    """

    agent_type = "event"
    description = (
        "Event agent — captures domain events, correlates sequences, detects patterns, drives temporal causality"
    )
    ddd_layer = "event"
    workload_spec = "self_healing"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._event_log: list[dict] = []
        self._patterns: list[tuple[str, Callable[[list[dict]], bool]]] = []
        self._event_counts: dict[str, int] = {}

    def register_pattern(self, name: str, detector: Callable[[list[dict]], bool]) -> None:
        self._patterns.append((name, detector))

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        event = context.get("event") or {
            "type": context.get("event_type", "unknown"),
            "source": context.get("source", ""),
            "payload": context.get("payload", {}),
            "timestamp": time.time(),
        }
        return {
            "layer": self.ddd_layer,
            "event": event,
            "log_size": len(self._event_log),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        event = perception.get("event", {})
        fired_patterns = [name for name, detector in self._patterns if detector(self._event_log + [event])]
        return {
            "layer": self.ddd_layer,
            "event": event,
            "fired_patterns": fired_patterns,
            "action": "record_and_notify",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        event = decision.get("event", {})
        event_type = event.get("type", "unknown")
        event["_recorded_at"] = time.time()
        self._event_log.append(event)
        self._event_counts[event_type] = self._event_counts.get(event_type, 0) + 1

        return {
            "action": "event_recorded",
            "event_type": event_type,
            "log_size": len(self._event_log),
            "fired_patterns": decision.get("fired_patterns", []),
            "event_counts": dict(self._event_counts),
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        top = sorted(self._event_counts.items(), key=lambda x: -x[1])[:3]
        if top:
            logger.debug("[event] top events: %s", top)
