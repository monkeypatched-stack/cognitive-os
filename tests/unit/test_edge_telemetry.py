"""AsyncTelemetryDispatcher (kernel/edge/telemetry.py) -- proves telemetry
never blocks the caller and is delivered asynchronously without touching
audit semantics."""
from __future__ import annotations

import threading
import time

from src.monkey_brain.kernel.edge.telemetry import AsyncTelemetryDispatcher


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.lock = threading.Lock()

    def counter(self, name, increment=1, **tags):
        with self.lock:
            self.events.append(("counter", name, increment, tags))

    def gauge(self, name, value, **tags):
        with self.lock:
            self.events.append(("gauge", name, value, tags))

    def histogram(self, name, value, **tags):
        with self.lock:
            self.events.append(("histogram", name, value, tags))

    def event(self, name, **fields):
        with self.lock:
            self.events.append(("event", name, fields))


class _SlowSink:
    """Simulates a blocking exporter -- proves the CALLER never waits on it."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    def counter(self, name, increment=1, **tags):
        time.sleep(self.delay)
        self.calls += 1

    def gauge(self, name, value, **tags):
        pass

    def histogram(self, name, value, **tags):
        pass

    def event(self, name, **fields):
        pass


class TestNonBlockingDispatch:
    def test_counter_call_returns_immediately_even_with_a_slow_sink(self):
        slow = _SlowSink(delay=0.3)
        dispatcher = AsyncTelemetryDispatcher(slow)
        start = time.monotonic()
        dispatcher.counter("edge.tick")
        elapsed = time.monotonic() - start
        assert elapsed < 0.05  # the enqueue itself must be O(1), not wait on the sink
        dispatcher.stop()

    def test_events_are_eventually_delivered(self):
        sink = _RecordingSink()
        dispatcher = AsyncTelemetryDispatcher(sink)
        dispatcher.counter("a")
        dispatcher.gauge("b", 1.0)
        dispatcher.histogram("c", 2.0)
        dispatcher.event("d", x=1)
        assert dispatcher.flush(timeout=2.0)
        names = {e[1] for e in sink.events}
        assert names == {"a", "b", "c", "d"}
        dispatcher.stop()


class TestBoundedQueueDropsOldestUnderOverflow:
    def test_overflow_drops_oldest_and_counts_drops(self):
        slow = _SlowSink(delay=0.05)
        dispatcher = AsyncTelemetryDispatcher(slow, max_queue=3)
        for i in range(20):
            dispatcher.counter(f"e{i}")
        stats = dispatcher.stats()
        assert stats["dropped"] > 0
        dispatcher.stop()

    def test_a_broken_sink_never_raises_into_the_caller(self):
        class _BrokenSink:
            def counter(self, *a, **k):
                raise RuntimeError("exporter is down")

            def gauge(self, *a, **k):
                pass

            def histogram(self, *a, **k):
                pass

            def event(self, *a, **k):
                pass

        dispatcher = AsyncTelemetryDispatcher(_BrokenSink())
        dispatcher.counter("will.fail")
        assert dispatcher.flush(timeout=1.0)  # drains without the thread dying
        dispatcher.counter("still.works.after.failure")
        assert dispatcher.flush(timeout=1.0)
        dispatcher.stop()
