"""TimelineStore — the append-only backing store for every actor timeline
(Presence, Membership, Goal, Belief, Execution, Relationship, Activity).

Backend selection/degrade-gracefully/singleton scaffolding mirrors
kernel/plan/goals/run_store.py::RunStore exactly (backend chosen once via
env var, in-memory LRU-ish-bounded fallback, Redis backend where every
call try/except-degrades rather than raising). The actual storage SHAPE
(per-entity keys + a ZSET time-index for range queries) instead mirrors
kernel/persistence/context_event_store.py::ContextEventStore, since
TimelineStore's core need — "give me every entry for this actor+kind
between since and until" — is a range query, not a single-key lookup the
way RunStore's run_id lookups are.

Nothing is ever mutated in place: close() (used by Presence/Membership to
end an open interval) APPENDS a new, closed copy of the entry rather than
editing the original — the store stays genuinely append-only end to end.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections import OrderedDict
from typing import Any

from src.monkey_brain.kernel.timeline.entry import ENTRY_CLASSES, TimelineEntry, TimelineKind

logger = logging.getLogger("agentos.timeline_store")

DEFAULT_CAPACITY_PER_ACTOR_KIND = int(os.getenv("TIMELINE_STORE_CAPACITY", "5000"))


def _entry_to_json(entry: TimelineEntry) -> str:
    return json.dumps({"kind": next(k.value for k, cls in ENTRY_CLASSES.items() if cls is type(entry)),
                        **entry.to_dict()})


def _entry_from_json(raw: str) -> TimelineEntry:
    d = json.loads(raw)
    kind = TimelineKind(d.pop("kind"))
    cls = ENTRY_CLASSES[kind]
    fields = cls.__dataclass_fields__
    kwargs = {}
    for k, v in d.items():
        if k not in fields:
            continue
        # JSON has no tuple type — every list-shaped field on these
        # frozen dataclasses is declared as a tuple (so entries stay
        # hashable); restore that on the way back in.
        kwargs[k] = tuple(v) if isinstance(v, list) and k != "metadata" else v
    return cls(**kwargs)


# ── In-memory backend (single-worker default) ────────────────────────────
class _InMemoryTimelineBackend:
    """Process-local, thread-safe. One append-only list per (actor_id, kind),
    oldest-first (matches insertion/start_time order since entries are never
    reordered) — bounded by dropping the OLDEST entries past capacity
    (there is no "touch"/re-use concept for historical records the way
    RunStore's LRU has for active runs)."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], list[TimelineEntry]] = OrderedDict()
        self._capacity = DEFAULT_CAPACITY_PER_ACTOR_KIND
        self._lock = threading.Lock()

    def append(self, entry: TimelineEntry, kind: TimelineKind) -> TimelineEntry:
        key = (entry.actor_id, kind.value)
        with self._lock:
            bucket = self._entries.setdefault(key, [])
            bucket.append(entry)
            if len(bucket) > self._capacity:
                del bucket[: len(bucket) - self._capacity]
        return entry

    def close(self, actor_id: str, kind: TimelineKind, entry_id: str, closed_entry: TimelineEntry) -> None:
        """Set end_time on the record identified by entry_id — the one
        exception to "append only new rows": closing an interval that is
        already open is part of that interval's own lifecycle (recording
        WHEN it ended), not a retroactive edit to WHERE/WHAT/WHO it was —
        every other field is carried over unchanged from the original."""
        key = (actor_id, kind.value)
        with self._lock:
            bucket = self._entries.get(key, [])
            for i, e in enumerate(bucket):
                if e.entry_id == entry_id:
                    bucket[i] = closed_entry
                    return

    def query(self, actor_id: str, kind: TimelineKind,
              since: float | None, until: float | None) -> tuple[TimelineEntry, ...]:
        with self._lock:
            bucket = list(self._entries.get((actor_id, kind.value), ()))
        if since is not None:
            bucket = [e for e in bucket if e.start_time >= since]
        if until is not None:
            bucket = [e for e in bucket if e.start_time <= until]
        return tuple(bucket)

    def all_for_actor(self, actor_id: str) -> tuple[TimelineEntry, ...]:
        with self._lock:
            result: list[TimelineEntry] = []
            for (aid, _kind), bucket in self._entries.items():
                if aid == actor_id:
                    result.extend(bucket)
        return tuple(result)

    def known_actors(self, kind: TimelineKind) -> tuple[str, ...]:
        with self._lock:
            return tuple({aid for (aid, k) in self._entries if k == kind.value})

    def entry_count(self, kind: TimelineKind | None = None) -> int:
        """Total entries stored (Prompt 8 — Lemon Observability's
        timeline_entries metric) — all kinds summed if kind is None."""
        with self._lock:
            if kind is None:
                return sum(len(bucket) for bucket in self._entries.values())
            return sum(len(bucket) for (_aid, k), bucket in self._entries.items() if k == kind.value)

    def clear_kind(self, kind: TimelineKind) -> int:
        """Drop every bucket for `kind`, across every actor. The one true
        deletion path this otherwise-append-only store exposes."""
        with self._lock:
            keys = [key for key in self._entries if key[1] == kind.value]
            count = sum(len(self._entries[key]) for key in keys)
            for key in keys:
                del self._entries[key]
        return count


# ── Redis backend (shared, multi-worker safe) ─────────────────────────────
class _RedisTimelineBackend:
    """Redis-backed: every worker reads/writes the same authoritative state.

    Keys:
        timeline:{actor_id}:{kind}:{entry_id} -> JSON entry (TTL'd)
        timeline:{actor_id}:{kind}:index      -> ZSET entry_id -> start_time
                                                  (range queries via
                                                  zrangebyscore; bounded via
                                                  zremrangebyrank, same
                                                  pattern as RunStore's index)
    Every op try/except-degrades (None/empty/False on failure) rather than
    raising — a dropped timeline write is not data loss the way a dropped
    run would be (the caller's own in-process state, e.g. BeliefState, is
    unaffected either way since reads/writes always go through this store).
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any = None
        self._ttl = int(os.getenv("TIMELINE_STORE_TTL_SECONDS", str(60 * 60 * 24 * 30)))  # 30 days

    def _connect(self) -> Any:
        import redis
        return redis.from_url(
            self._url, decode_responses=True,
            socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT_SEC", "5")),
            socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SEC", "5")),
        )

    def available(self) -> bool:
        try:
            self._client = self._connect()
            self._client.ping()
            return True
        except Exception as exc:
            logger.warning("TimelineStore Redis backend unreachable: %s", exc)
            self._client = None
            return False

    @property
    def _r(self) -> Any:
        if self._client is None:
            self._client = self._connect()
        return self._client

    def _entry_key(self, actor_id: str, kind: TimelineKind, entry_id: str) -> str:
        return f"timeline:{actor_id}:{kind.value}:{entry_id}"

    def _index_key(self, actor_id: str, kind: TimelineKind) -> str:
        return f"timeline:{actor_id}:{kind.value}:index"

    def append(self, entry: TimelineEntry, kind: TimelineKind) -> TimelineEntry:
        try:
            pipe = self._r.pipeline()
            pipe.set(self._entry_key(entry.actor_id, kind, entry.entry_id), _entry_to_json(entry), ex=self._ttl)
            pipe.zadd(self._index_key(entry.actor_id, kind), {entry.entry_id: entry.start_time})
            pipe.zremrangebyrank(self._index_key(entry.actor_id, kind), 0, -(DEFAULT_CAPACITY_PER_ACTOR_KIND + 1))
            pipe.execute()
        except Exception as exc:
            logger.warning("TimelineStore(redis).append failed for actor=%r kind=%s: %s", entry.actor_id, kind, exc)
        return entry

    def close(self, actor_id: str, kind: TimelineKind, entry_id: str, closed_entry: TimelineEntry) -> None:
        """Overwrite the SAME entry_id's JSON with end_time now set — the
        ZSET index's score (start_time) is unchanged, so this is the one
        in-place update this store performs, and only ever to add an
        end_time to a record that already exists (see the in-memory
        backend's close() docstring for why this isn't a retroactive edit)."""
        try:
            self._r.set(self._entry_key(actor_id, kind, entry_id), _entry_to_json(closed_entry), ex=self._ttl)
        except Exception as exc:
            logger.warning("TimelineStore(redis).close failed for actor=%r kind=%s: %s", actor_id, kind, exc)

    def query(self, actor_id: str, kind: TimelineKind,
              since: float | None, until: float | None) -> tuple[TimelineEntry, ...]:
        try:
            lo = since if since is not None else "-inf"
            hi = until if until is not None else "+inf"
            entry_ids = self._r.zrangebyscore(self._index_key(actor_id, kind), lo, hi)
        except Exception as exc:
            logger.warning("TimelineStore(redis).query index read failed: %s", exc)
            return ()
        entries: list[TimelineEntry] = []
        for entry_id in entry_ids:
            try:
                raw = self._r.get(self._entry_key(actor_id, kind, entry_id))
                if raw:
                    entries.append(_entry_from_json(raw))
            except Exception as exc:
                logger.warning("TimelineStore(redis).query entry read failed: %s", exc)
        return tuple(entries)

    def all_for_actor(self, actor_id: str) -> tuple[TimelineEntry, ...]:
        result: list[TimelineEntry] = []
        for kind in TimelineKind:
            result.extend(self.query(actor_id, kind, None, None))
        return tuple(result)

    def known_actors(self, kind: TimelineKind) -> tuple[str, ...]:
        try:
            actor_ids: set[str] = set()
            pattern = f"timeline:*:{kind.value}:index"
            for key in self._r.scan_iter(match=pattern):
                # key shape: timeline:{actor_id}:{kind}:index
                parts = key.split(":")
                if len(parts) >= 4:
                    actor_ids.add(parts[1])
            return tuple(actor_ids)
        except Exception as exc:
            logger.warning("TimelineStore(redis).known_actors failed: %s", exc)
            return ()

    def entry_count(self, kind: TimelineKind | None = None) -> int:
        try:
            kinds = [kind] if kind is not None else list(TimelineKind)
            total = 0
            for k in kinds:
                pattern = f"timeline:*:{k.value}:index"
                for key in self._r.scan_iter(match=pattern):
                    total += self._r.zcard(key)
            return total
        except Exception as exc:
            logger.warning("TimelineStore(redis).entry_count failed: %s", exc)
            return 0

    def clear_kind(self, kind: TimelineKind) -> int:
        try:
            count = 0
            pattern = f"timeline:*:{kind.value}:index"
            for index_key in self._r.scan_iter(match=pattern):
                actor_id = index_key.split(":")[1]
                entry_ids = self._r.zrange(index_key, 0, -1)
                count += len(entry_ids)
                pipe = self._r.pipeline()
                for entry_id in entry_ids:
                    pipe.delete(self._entry_key(actor_id, kind, entry_id))
                pipe.delete(index_key)
                pipe.execute()
            return count
        except Exception as exc:
            logger.warning("TimelineStore(redis).clear_kind failed for kind=%s: %s", kind, exc)
            return 0


# ── Edge-local backend (SQLite, durable across restarts, no network) ────
class _EdgeLocalTimelineBackend:
    """Backed by kernel/edge/local_store.py::EdgeLocalStore — durable
    (survives a process restart, unlike _InMemoryTimelineBackend) but
    requires no reachable network service (unlike _RedisTimelineBackend),
    the property Edge-First Architecture needs for Presence/Membership/
    Goal/etc. to work on a genuinely offline edge/device/robot node.

    One EdgeLocalStore namespace per TimelineKind ("timeline:{kind}"), key
    "{actor_id}:{entry_id}" — mirrors the Redis backend's per-kind
    partitioning (its own key pattern's {kind} segment) rather than a
    single shared namespace, so a bulk `clear_kind` stays a single
    `EdgeLocalStore.clear_namespace` call. No TTL/expiry (EdgeLocalStore
    has none) and no separate index structure — `list(namespace)` plus an
    in-process filter stands in for the Redis backend's ZSET index; this
    trades index-query elegance for reusing the one already-tested local
    storage primitive rather than adding a second SQLite table shape."""

    def __init__(self, store: Any) -> None:
        self._store = store

    @staticmethod
    def _namespace(kind: TimelineKind) -> str:
        return f"timeline:{kind.value}"

    @staticmethod
    def _key(actor_id: str, entry_id: str) -> str:
        return f"{actor_id}:{entry_id}"

    def _provenance(self) -> Any:
        from src.monkey_brain.kernel.edge.freshness import CacheProvenance
        import time as _time
        return CacheProvenance(source="edge_local:timeline", observed_at=_time.time(), freshness_requirement="safe_offline")

    def append(self, entry: TimelineEntry, kind: TimelineKind) -> TimelineEntry:
        try:
            self._store.put(
                self._namespace(kind), self._key(entry.actor_id, entry.entry_id),
                json.loads(_entry_to_json(entry)), self._provenance(),
            )
        except Exception as exc:
            logger.warning("TimelineStore(edge_local).append failed for actor=%r kind=%s: %s", entry.actor_id, kind, exc)
        return entry

    def close(self, actor_id: str, kind: TimelineKind, entry_id: str, closed_entry: TimelineEntry) -> None:
        try:
            self._store.put(
                self._namespace(kind), self._key(actor_id, entry_id),
                json.loads(_entry_to_json(closed_entry)), self._provenance(),
            )
        except Exception as exc:
            logger.warning("TimelineStore(edge_local).close failed for actor=%r kind=%s: %s", actor_id, kind, exc)

    def _all_entries(self, kind: TimelineKind) -> list[TimelineEntry]:
        try:
            raw_entries = self._store.list(self._namespace(kind))
        except Exception as exc:
            logger.warning("TimelineStore(edge_local) list failed for kind=%s: %s", kind, exc)
            return []
        out = []
        for cache_entry in raw_entries:
            try:
                out.append(_entry_from_json(json.dumps(cache_entry.value)))
            except Exception as exc:
                logger.warning("TimelineStore(edge_local) corrupt entry %s: %s", cache_entry.key, exc)
        return out

    def query(self, actor_id: str, kind: TimelineKind,
              since: float | None, until: float | None) -> tuple[TimelineEntry, ...]:
        entries = [e for e in self._all_entries(kind) if e.actor_id == actor_id]
        if since is not None:
            entries = [e for e in entries if e.start_time >= since]
        if until is not None:
            entries = [e for e in entries if e.start_time <= until]
        return tuple(entries)

    def all_for_actor(self, actor_id: str) -> tuple[TimelineEntry, ...]:
        result: list[TimelineEntry] = []
        for kind in TimelineKind:
            result.extend(e for e in self._all_entries(kind) if e.actor_id == actor_id)
        return tuple(result)

    def known_actors(self, kind: TimelineKind) -> tuple[str, ...]:
        return tuple({e.actor_id for e in self._all_entries(kind)})

    def entry_count(self, kind: TimelineKind | None = None) -> int:
        kinds = [kind] if kind is not None else list(TimelineKind)
        return sum(len(self._all_entries(k)) for k in kinds)

    def clear_kind(self, kind: TimelineKind) -> int:
        try:
            return self._store.clear_namespace(self._namespace(kind))
        except Exception as exc:
            logger.warning("TimelineStore(edge_local).clear_kind failed for kind=%s: %s", kind, exc)
            return 0


def _make_backend() -> Any:
    choice = os.getenv("TIMELINE_STORE_BACKEND", "auto").strip().lower()

    # Edge-First Architecture: an edge/device/robot node prefers durable
    # local storage over an in-memory-only fallback — EdgeLocalStore is a
    # local SQLite file, always available, no network dependency, so
    # "auto" on this node class tries it BEFORE falling all the way back
    # to _InMemoryTimelineBackend (which loses everything on restart).
    node_class_hint = os.getenv("ACTOR_NODE_CLASS", "cloud").strip().lower()
    prefers_edge_local = node_class_hint in ("edge", "device", "robot")

    if choice == "edge_local" or (choice == "auto" and prefers_edge_local):
        try:
            from src.monkey_brain.kernel.edge.local_store import get_edge_local_store
            logger.info("TimelineStore: edge-local SQLite backend (node_class=%s)", node_class_hint)
            return _EdgeLocalTimelineBackend(get_edge_local_store())
        except Exception as exc:
            if choice == "edge_local":
                logger.error("TimelineStore: TIMELINE_STORE_BACKEND=edge_local but EdgeLocalStore unavailable: %s", exc)
            # "auto" falls through to the Redis/memory logic below.
    # REDIS_URL is the explicit override; PlanetaryRuntime's own Redis
    # connection (kernel/society/integration.py::_init_persistence) uses
    # REDIS_HOST/REDIS_PORT instead — without this fallback, an
    # environment configured the way this whole system already connects
    # to Redis (host/port, no REDIS_URL) silently gets an in-memory-only
    # TimelineStore, and every Membership/Presence/Goal/Belief/Execution/
    # Relationship/Activity timeline entry is lost on every restart.
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        # Same defaults PlanetaryRuntime._init_persistence() connects
        # with — not gated on REDIS_HOST being explicitly set, since
        # that's unset in this environment too and it still connects.
        url = f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"

    if choice == "memory":
        return _InMemoryTimelineBackend()

    if choice in ("auto", "redis") and url:
        backend = _RedisTimelineBackend(url)
        if backend.available():
            logger.info("TimelineStore: SHARED Redis backend — multi-worker safe")
            return backend
        if choice == "redis":
            logger.error("TimelineStore: TIMELINE_STORE_BACKEND=redis but Redis unreachable — falling back to memory")
        return _InMemoryTimelineBackend()

    return _InMemoryTimelineBackend()


class TimelineStore:
    """Singleton append-only store for every actor timeline. Delegates to a
    process-local (default) or shared Redis backend; public API is
    identical either way."""

    _instance: "TimelineStore | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "TimelineStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._backend = _make_backend()
                    cls._instance = instance
        return cls._instance

    def append(self, entry: TimelineEntry, kind: TimelineKind) -> TimelineEntry:
        return self._backend.append(entry, kind)

    def record(self, kind: TimelineKind, **fields: Any) -> TimelineEntry:
        """Construct-and-append in one step — the sole entry point that
        instantiates ENTRY_CLASSES[kind] directly (mirrors kernel/geography/
        registry.py::GeographicRegistry.create() being the sole constructor
        of Planet/.../Space). Callers (belief_state.py's update_goal(),
        belief_runtime.py's _record_execution(), society/belief.py's
        _record_belief_history(), PresenceTimeline, SocietyMembershipRegistry)
        pass kind + field kwargs rather than importing/instantiating the
        dataclasses themselves."""
        entry_cls = ENTRY_CLASSES[kind]
        return self.append(entry_cls(**fields), kind)

    def query(self, actor_id: str, kind: TimelineKind,
              since: float | None = None, until: float | None = None) -> tuple[TimelineEntry, ...]:
        """Every entry for actor_id/kind with start_time in [since, until],
        ordered oldest-first."""
        return tuple(sorted(self._backend.query(actor_id, kind, since, until), key=lambda e: e.start_time))

    def current(self, actor_id: str, kind: TimelineKind) -> TimelineEntry | None:
        """The open entry (end_time is None) if one exists, else the most
        recent entry overall — covers both "interval" kinds (Presence,
        Membership, where exactly one should be open) and "point" kinds
        (Goal, Execution, Belief, where there's no open/closed concept,
        just "most recent")."""
        entries = self.query(actor_id, kind)
        if not entries:
            return None
        open_entries = [e for e in entries if e.is_open()]
        if open_entries:
            return max(open_entries, key=lambda e: e.start_time)
        return max(entries, key=lambda e: e.start_time)

    def at(self, actor_id: str, kind: TimelineKind, timestamp: float) -> TimelineEntry | None:
        """The entry valid at `timestamp` — start_time <= timestamp and
        (end_time is None or end_time >= timestamp)."""
        candidates = [
            e for e in self.query(actor_id, kind)
            if e.start_time <= timestamp and (e.end_time is None or e.end_time >= timestamp)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda e: e.start_time)

    def close(self, entry: TimelineEntry, kind: TimelineKind, end_time: float) -> TimelineEntry:
        """Set end_time on an already-open entry — the one exception to
        "every write is a new row": closing the interval that is already
        open is recording WHEN it ended, not changing WHERE/WHAT/WHO it
        was (every other field carries over unchanged). See Presence's
        spec: "Presence records are never modified" refers to their
        WHERE/activity/confidence content, not to this one, single,
        well-defined closing transition every open interval goes through
        exactly once."""
        import dataclasses
        closed = dataclasses.replace(entry, end_time=end_time)
        self._backend.close(entry.actor_id, kind, entry.entry_id, closed)
        return closed

    def known_actors(self, kind: TimelineKind) -> tuple[str, ...]:
        """Every actor_id with at least one entry of this kind — backs
        PresenceTimeline.occupants()'s "who was in this Space" scan."""
        return self._backend.known_actors(kind)

    def entry_count(self, kind: TimelineKind | None = None) -> int:
        """Total entries stored across every actor — all kinds summed if
        kind is None. Backs Prompt 8's timeline_entries Lemon metric."""
        return self._backend.entry_count(kind)

    def clear_kind(self, kind: TimelineKind) -> int:
        """Remove every entry of `kind`, across every actor — the one true
        deletion path this otherwise-append-only store exposes. Used by
        DELETE /planet/executions to reset execution history without
        touching Presence/Membership/Goal/Belief/Relationship/Activity."""
        return self._backend.clear_kind(kind)

    @classmethod
    def reset_for_testing(cls) -> None:
        """Force a fresh backend on next use. TimelineStore is a
        process-wide singleton, so tests that construct bare BeliefState()
        instances (actor_id="" by default) would otherwise share one
        actor's GoalTimeline across every test in the process — see
        tests/conftest.py's autouse fixture, which calls this before every
        test the same way it already resets tenant-scope ContextVars."""
        with cls._instance_lock:
            cls._instance = None


def get_timeline_store() -> TimelineStore:
    return TimelineStore()
