"""Asynchronous, non-blocking telemetry dispatch for the edge hot path.

This is ONLY for non-critical observability data (counters/gauges/
histograms/events routed to kernel/compile/_obs.py, which in turn is a
silent no-op unless Lemon is attached). It must NEVER be used for
anything kernel/audit.py::AuditLog.record is responsible for -- audit
records are the fail-closed, synchronous, "must exist before the mutation
is trusted" primitive and this module does not touch audit.py at all.

The problem this solves: _obs.counter()/gauge()/histogram() are called
directly on the hot path (kernel/pipeline/action_executor.py) and, when a
real exporter is attached, may block on network I/O. A single slow
telemetry export must never add latency to a governed capability call.

AsyncTelemetryDispatcher decouples "record that this happened" (an O(1)
bounded-queue push on the calling thread) from "actually export it" (a
background drain thread). Under sustained overflow it drops the OLDEST
buffered events and counts the drops -- telemetry loss is an acceptable,
observable degradation; it is never allowed to become back-pressure on
governance or execution.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class _Event:
    kind: str  # "counter" | "gauge" | "histogram" | "event"
    name: str
    value: Any
    tags: dict[str, Any]


class AsyncTelemetryDispatcher:
    """Bounded, drop-oldest, background-drained telemetry queue.

    `sink` is anything exposing counter(name, increment=1, **tags),
    gauge(name, value, **tags), histogram(name, value, **tags),
    event(name, **fields) -- e.g. kernel/compile/_obs itself, or a
    Recorder in tests. This class never constructs a sink of its own.
    """

    def __init__(self, sink: Any, *, max_queue: int = 2048) -> None:
        self._sink = sink
        self._max_queue = max_queue
        self._queue: deque[_Event] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._dropped = 0
        self._delivered = 0
        self._stopped = False
        self._thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._thread.start()

    def _enqueue(self, event: _Event) -> None:
        with self._lock:
            if len(self._queue) >= self._max_queue:
                self._queue.popleft()
                self._dropped += 1
            self._queue.append(event)
            self._not_empty.notify()

    def counter(self, name: str, increment: int = 1, **tags: Any) -> None:
        self._enqueue(_Event("counter", name, increment, tags))

    def gauge(self, name: str, value: float, **tags: Any) -> None:
        self._enqueue(_Event("gauge", name, value, tags))

    def histogram(self, name: str, value: float, **tags: Any) -> None:
        self._enqueue(_Event("histogram", name, value, tags))

    def event(self, name: str, **fields: Any) -> None:
        self._enqueue(_Event("event", name, None, fields))

    def _drain_loop(self) -> None:
        while True:
            with self._lock:
                while not self._queue and not self._stopped:
                    self._not_empty.wait(timeout=0.5)
                if self._stopped and not self._queue:
                    return
                item = self._queue.popleft() if self._queue else None
            if item is None:
                continue
            self._deliver(item)

    def _deliver(self, item: _Event) -> None:
        try:
            if item.kind == "counter":
                self._sink.counter(item.name, increment=item.value, **item.tags)
            elif item.kind == "gauge":
                self._sink.gauge(item.name, item.value, **item.tags)
            elif item.kind == "histogram":
                self._sink.histogram(item.name, item.value, **item.tags)
            elif item.kind == "event":
                self._sink.event(item.name, **item.tags)
            self._delivered += 1
        except Exception:
            pass  # a broken exporter must never propagate onto the drain thread's caller

    def flush(self, timeout: float = 2.0) -> bool:
        """Block until the queue drains or timeout -- for tests and clean shutdown, not the hot path."""
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._queue:
                    return True
            time.sleep(0.005)
        with self._lock:
            return not self._queue

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._not_empty.notify_all()
        self._thread.join(timeout=2.0)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"queued": len(self._queue), "dropped": self._dropped, "delivered": self._delivered}
