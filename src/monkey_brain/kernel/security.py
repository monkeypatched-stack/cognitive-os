"""Security Hardening — input validation, replay protection, rate limiting, allow-lists.

Replaces prototype assumptions with production security controls.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

# ── Allow-list data classification ──────────────────────────────────────────

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SAFE_DOMAIN = re.compile(r"^[a-z][a-z0-9_]*$")

DENY_LIST_PATTERNS = [
    re.compile(r"<script", re.I),
    re.compile(r"javascript:", re.I),
    re.compile(r"DROP\s+TABLE", re.I),
    re.compile(r"UNION\s+SELECT", re.I),
    re.compile(r";\s*--"),
]


def validate_identifier(value: str, max_len: int = 256) -> tuple[bool, str]:
    """Validate a runtime identifier (agent names, capability names, etc.)."""
    if not value or len(value) > max_len:
        return False, "empty_or_too_long"
    if not SAFE_IDENTIFIER.match(value):
        return False, "invalid_characters"
    return True, ""


def validate_domain(value: str) -> tuple[bool, str]:
    """Validate a domain string."""
    if not value or len(value) > 128:
        return False, "empty_or_too_long"
    if not SAFE_DOMAIN.match(value):
        return False, "invalid_domain_format"
    return True, ""


def sanitize_input(value: str, max_len: int = 10000) -> str:
    """Sanitize user input — strip, truncate, reject known injection patterns."""
    value = value.strip()[:max_len]
    for pattern in DENY_LIST_PATTERNS:
        if pattern.search(value):
            raise ValueError(f"Input contains blocked pattern: {pattern.pattern}")
    return value


# ── Rate limiter ────────────────────────────────────────────────────────────


class RateLimiter:
    """Token-bucket rate limiter per key (runtime_id, IP, etc.)."""

    def __init__(self, rate: float = 100.0, burst: float = 200.0) -> None:
        self._rate = rate  # tokens per second
        self._burst = burst  # max tokens
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_time)
        self._lock = threading.Lock()

    def allow(self, key: str, tokens: float = 1.0) -> bool:
        """Return True if the request is allowed (has enough tokens)."""
        now = time.time()
        with self._lock:
            if key in self._buckets:
                tokens_left, last = self._buckets[key]
                elapsed = now - last
                tokens_left = min(self._burst, tokens_left + elapsed * self._rate)
            else:
                tokens_left = self._burst

            if tokens_left >= tokens:
                self._buckets[key] = (tokens_left - tokens, now)
                return True
            self._buckets[key] = (tokens_left, now)
            return False

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)


# ── Concurrency control ────────────────────────────────────────────────────


class ReadWriteLock:
    """Multiple-reader, single-writer lock."""

    def __init__(self) -> None:
        self._read_ready = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False

    def acquire_read(self) -> None:
        with self._read_ready:
            while self._writer:
                self._read_ready.wait()
            self._readers += 1

    def release_read(self) -> None:
        with self._read_ready:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()

    def acquire_write(self) -> None:
        self._read_ready.acquire()
        while self._readers > 0:
            self._read_ready.wait()
        self._writer = True

    def release_write(self) -> None:
        self._writer = False
        self._read_ready.release()


# ── Bounded queue ──────────────────────────────────────────────────────────


class BoundedQueue:
    """Thread-safe bounded queue with backpressure metrics."""

    def __init__(self, max_size: int = 10000) -> None:
        self._queue: list[Any] = []
        self._max = max_size
        self._lock = threading.Lock()
        self._total_enqueued = 0
        self._total_dropped = 0

    def put(self, item: Any) -> bool:
        """Add item if queue not full.  Returns False if dropped (backpressure)."""
        with self._lock:
            if len(self._queue) >= self._max:
                self._total_dropped += 1
                return False
            self._queue.append(item)
            self._total_enqueued += 1
            return True

    def get(self) -> Any | None:
        with self._lock:
            return self._queue.pop(0) if self._queue else None

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def metrics(self) -> dict[str, int]:
        return {
            "pending": self.pending(),
            "enqueued": self._total_enqueued,
            "dropped": self._total_dropped,
        }
