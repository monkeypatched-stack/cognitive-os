"""Context Stream (Step 12.8) — the backbone of the society.

Every significant event becomes a context event:
    observation, belief update, action, interaction,
    learning, prediction, world update.

Support:
    replay, audit, temporal reasoning, provenance.

Context becomes append-only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger("agentos.society.context_stream")


class ContextEventType(Enum):
    OBSERVATION = "observation"
    BELIEF_UPDATE = "belief_update"
    ACTION = "action"
    INTERACTION = "interaction"
    LEARNING = "learning"
    PREDICTION = "prediction"
    WORLD_UPDATE = "world_update"
    SOCIETY_TICK = "society_tick"
    TEMPORARY_MEMBERSHIP_GRANTED = "temporary_membership_granted"
    TEMPORARY_MEMBERSHIP_REVOKED = "temporary_membership_revoked"
    ACTOR_LIFECYCLE = "actor_lifecycle"
    """Actor Lifecycle Controller: one event per lifecycle transition
    (ActorStarting/ActorReady/ActorSuspended/ActorFailed/etc — see
    actor_lifecycle.py's LifecycleEvent). Distinct from ACTION (a
    capability the actor's cognition executed) and WORLD_UPDATE (a change
    to shared reality) — this is the controller managing the actor as a
    deployment unit, not the actor acting in the world."""


@dataclass(frozen=True)
class ContextEvent:
    """One event in the context stream. Append-only."""

    event_id: str = field(default_factory=lambda: uuid4().hex)
    event_type: ContextEventType = ContextEventType.WORLD_UPDATE
    actor_id: str = ""
    description: str = ""
    payload: Any = None
    confidence: float = 1.0
    provenance: str = ""
    """Where this event originated."""
    timestamp: float = field(default_factory=time.time)
    version: int = 0
    correlation_id: str = ""
    """Id of the logical end-to-end operation this event belongs to (e.g.
    an execution_id/transaction_id/AskActor request id), when known."""
    causation_id: str = ""
    """Id of the immediate upstream record that caused this event (e.g. a
    CommunicationDecision.decision_id or a negotiation round's trace_id).
    Left empty rather than fabricated when no concrete cause is in scope."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "actor_id": self.actor_id,
            "description": self.description,
            "payload": self.payload,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "timestamp": self.timestamp,
            "version": self.version,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ContextEvent":
        return cls(
            event_id=d.get("event_id", uuid4().hex),
            event_type=(ContextEventType(d["event_type"]) if "event_type" in d else ContextEventType.WORLD_UPDATE),
            actor_id=d.get("actor_id", ""),
            description=d.get("description", ""),
            payload=d.get("payload"),
            confidence=d.get("confidence", 1.0),
            provenance=d.get("provenance", ""),
            timestamp=d.get("timestamp", 0.0),
            version=d.get("version", 0),
            correlation_id=d.get("correlation_id", ""),
            causation_id=d.get("causation_id", ""),
        )


@dataclass(frozen=True)
class ContextSnapshot:
    """A point-in-time snapshot of the context stream for replay."""

    snapshot_id: str = field(default_factory=lambda: uuid4().hex)
    version: int = 0
    event_count: int = 0
    timestamp: float = field(default_factory=time.time)


class SocietyContextStream:
    """Append-only context stream for the society. Every significant
    event is recorded here for replay, audit, and temporal reasoning."""

    def __init__(self, max_history: int = 100000) -> None:
        self._events: list[ContextEvent] = []
        self._version = 0
        self._subscribers: list[Callable[[ContextEvent], None]] = []
        self._max_history = max_history
        self._on_publish: Callable[[], None] | None = None
        self._dirty = False
        self._nats_client: Any = None
        self._nats_subject: str = ""
        # A Task with no other reference can be garbage-collected before
        # it completes (same pattern api/routes/prompt.py already uses
        # for its own background tasks) — keep one here.
        self._nats_tasks: set[asyncio.Task] = set()

    def set_on_publish(self, callback: Callable[[], None]) -> None:
        self._on_publish = callback

    def set_nats(self, client: Any, subject: str) -> None:
        """Real NATS publishing (the TODO on publish() below) — a live
        pub/sub channel alongside the existing Redis durability
        (_save_context, wired via set_on_publish), not a replacement for
        it. Optional: client may be None to disable, same "non-required
        resource" contract as kernel/resources/nats.py's own NATS
        health check."""
        self._nats_client = client
        self._nats_subject = subject

    def clear(self) -> int:
        """Discard every event in this stream. Purely in-memory (no
        Redis/Timeline persistence for ContextEvents — see the TODO on
        publish() below), so this is safe/non-destructive to any real,
        persisted cognitive state (actors, beliefs, plans, decisions,
        execution history) — only the transient Event Stream Panel feed
        is affected. Returns how many were discarded."""
        count = len(self._events)
        self._events = []
        return count

    @property
    def version(self) -> int:
        return self._version

    @property
    def event_count(self) -> int:
        return len(self._events)

    def publish(self, event: ContextEvent) -> ContextEvent:
        self._version += 1
        stamped = ContextEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            actor_id=event.actor_id,
            description=event.description,
            payload=event.payload,
            confidence=event.confidence,
            provenance=event.provenance,
            timestamp=event.timestamp,
            version=self._version,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
        )

        self._events.append(stamped)
        if len(self._events) > self._max_history:
            self._events = self._events[-self._max_history :]
        self._dirty = True
        self._validate_causal_lineage(stamped)
        if self._nats_client is not None:
            # Fire-and-forget: publish() is called from many existing
            # sync call sites across the codebase — changing it to a
            # coroutine is out of scope. A live telemetry channel is
            # correctly best-effort; the existing _save_context path
            # below is still the durable record.
            try:
                task = asyncio.create_task(
                    self._nats_client.publish(
                        self._nats_subject,
                        json.dumps(stamped.to_dict()).encode(),
                    )
                )
                self._nats_tasks.add(task)
                task.add_done_callback(self._nats_tasks.discard)
            except RuntimeError:
                logger.debug("publish: no running event loop for NATS publish", exc_info=True)
        if self._on_publish is not None:
            try:
                self._on_publish()
            except Exception:
                logger.debug("publish: _on_publish callback failed", exc_info=True)
        for subscriber in self._subscribers:
            try:
                subscriber(stamped)
            except Exception:
                logger.debug("publish: subscriber %r failed", subscriber, exc_info=True)
        return stamped

    def _validate_causal_lineage(self, event: ContextEvent) -> None:
        """CognitiveOS Constitution: "causal dependencies are runtime
        invariants." correlation_id/causation_id were, until this check,
        purely optional str="" fields threaded by convention at real call
        sites — nothing anywhere validated that a downstream event
        (one with a causation_id, meaning something upstream produced it)
        actually carries the correlation_id of the end-to-end operation it
        belongs to. Without that, the causal chain can be pointed-at
        (causation_id) but never walked back to the operation it's part
        of (correlation_id).

        This is deliberately non-blocking: many existing call sites don't
        set these fields yet, and rejecting/mutating the event here would
        turn an observability gap into a functional regression. The point
        is to make the previously-silent gap OBSERVABLE (log + metric),
        not to enforce it by fiat before every caller has been audited."""
        if event.causation_id and not event.correlation_id:
            from src.monkey_brain.kernel.compile import _obs

            logger.warning(
                "context_stream: event %s (%s, provenance=%r) has causation_id=%r but no "
                "correlation_id -- causal chain cannot be traced back to its originating operation",
                event.event_id,
                event.event_type.value,
                event.provenance,
                event.causation_id,
            )
            _obs.counter(
                "context_stream.causal_lineage_violations",
                event_type=event.event_type.value,
                provenance=event.provenance,
            )

    def subscribe(self, callback: Callable[[ContextEvent], None]) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[ContextEvent], None]) -> None:
        self._subscribers = [s for s in self._subscribers if s is not callback]

    def replay(
        self,
        from_version: int = 0,
        to_version: int | None = None,
        event_type: ContextEventType | None = None,
        actor_id: str | None = None,
    ) -> tuple[ContextEvent, ...]:
        events = self._events
        if from_version > 0:
            events = [e for e in events if e.version >= from_version]
        if to_version is not None:
            events = [e for e in events if e.version <= to_version]
        if event_type is not None:
            events = [e for e in events if e.event_type == event_type]
        if actor_id is not None:
            events = [e for e in events if e.actor_id == actor_id]
        return tuple(events)

    def events(self, limit: int = 100) -> tuple[ContextEvent, ...]:
        return tuple(self._events[-limit:])

    def snapshot(self) -> ContextSnapshot:
        return ContextSnapshot(
            version=self._version,
            event_count=len(self._events),
        )

    def audit_trail(self, event_id: str) -> ContextEvent | None:
        for event in self._events:
            if event.event_id == event_id:
                return event
        return None

    def provenance_for(self, actor_id: str) -> tuple[ContextEvent, ...]:
        return tuple(e for e in self._events if e.actor_id == actor_id)
