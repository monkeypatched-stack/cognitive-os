"""BoundedTTLCache — the one in-process (L1) caching primitive every
edge-hot-path cache in this package builds on: delegation-verification
reuse, ContextConstructionEngine memoization, semantic-retrieval
memoization. One implementation, reused, rather than four ad hoc caches
with four slightly different invalidation bugs.

Deliberately NOT an "unbounded authorization cache" (explicitly forbidden
by this task): bounded by `max_size` (LRU eviction) AND by an explicit
`version_key` per entry — whatever the caller encodes into that key
(policy_version + authority_epoch + revocation_generation + ...) is
compared on every read, so a cached entry becomes unusable the instant
ANY of those inputs changes, without this module needing to know what
"policy" or "delegation" or "epoch" even mean. TTL is a second,
independent bound — an entry is unusable once EITHER its version_key no
longer matches OR its TTL has elapsed, whichever comes first.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

V = TypeVar("V")


@dataclass(frozen=True)
class _Entry(Generic[V]):
    value: V
    version_key: str
    expires_at: float


class BoundedTTLCache(Generic[V]):
    def __init__(self, *, max_size: int = 1024, default_ttl_seconds: float = 30.0) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl_seconds
        self._entries: "OrderedDict[str, _Entry[V]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str, *, version_key: str, now: float | None = None) -> V | None:
        """Returns the cached value only if BOTH the version_key matches
        exactly and the entry has not expired -- a version mismatch is
        treated identically to a miss (evicted immediately, never
        returned stale-but-close)."""
        now = time.time() if now is None else now
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.version_key != version_key or now > entry.expires_at:
                del self._entries[key]
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return entry.value

    def put(self, key: str, value: V, *, version_key: str, ttl_seconds: float | None = None) -> None:
        ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        with self._lock:
            self._entries[key] = _Entry(value=value, version_key=version_key, expires_at=time.time() + ttl)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_size:
                self._entries.popitem(last=False)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def invalidate_version(self, version_key: str) -> int:
        """Drop every entry stamped with this exact version_key --
        useful when a caller wants to proactively clear everything tied
        to a superseded epoch/policy version rather than waiting for
        each entry's own lazy check on next read. Returns count removed."""
        with self._lock:
            stale = [k for k, e in self._entries.items() if e.version_key == version_key]
            for k in stale:
                del self._entries[k]
            return len(stale)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "hits": self.hits, "misses": self.misses, "size": self.size,
            "hit_rate": (self.hits / total) if total else 0.0,
        }
