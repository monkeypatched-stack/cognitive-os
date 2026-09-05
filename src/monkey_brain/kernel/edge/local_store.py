"""EdgeLocalStore — durable, embedded local storage for an Actor
Runtime/Edge Node.

Central stores (Mongo/Redis/Neo4j/Elasticsearch) remain authoritative;
this is a durable LOCAL CACHE / execution-state store, appropriate for a
device or robot that cannot assume a connection to any of them. SQLite is
used deliberately -- it is already a Python stdlib dependency (no new
package, no daemon process, single file, safe for a battery-powered/
intermittently-connected device), unlike every store this codebase
otherwise uses, all of which assume a reachable network service.

Kept behind this one abstraction (`EdgeLocalStore`) so the backend can
change later (e.g. an actual embedded KV store on more constrained
hardware) without touching any caller -- every caller in kernel/edge/
speaks this class's methods, never sqlite3 directly.

Table shape is deliberately generic (one `cache_entries` table + one
`sync_state` table), not one table per concept (belief/policy/negotiation/
etc.) -- Section 1's own list is long and will grow; a `namespace` column
partitions it instead of requiring a schema migration for each new kind
of cached thing. `namespace` values already anticipated by kernel/edge/
callers: "belief", "world_projection", "capability_metadata",
"policy_snapshot", "delegation", "negotiation", "semantic_memory",
"idempotency".
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator

from src.monkey_brain.kernel.edge.freshness import CacheProvenance, provenance_from_dict, provenance_to_dict

logger = logging.getLogger("agentos.edge.local_store")

_DEFAULT_DB_PATH = os.path.expanduser("~/.monkeybrain/edge/local_store.db")


@dataclass(frozen=True)
class CacheEntry:
    """One durable local record: a value plus the provenance needed to
    judge its freshness later (kernel/edge/freshness.py)."""
    namespace: str
    key: str
    value: dict[str, Any]
    provenance: CacheProvenance
    updated_at: float


class EdgeLocalStoreError(RuntimeError):
    """Raised only for a genuine local storage fault (disk I/O, corrupt
    DB file) -- never for "key not found" (that's a normal None/[] return,
    not an error) and never used to smuggle a governance decision."""


class EdgeLocalStore:
    """Thread-safe, single-file SQLite-backed cache + execution-state
    store. One instance per Actor Runtime process, matching the existing
    one-actor-per-process precedent (ACTOR_NODE_CAPACITY=1 for edge/
    device/robot nodes -- docs/CLOUD_EDGE_ACTOR_ARCHITECTURE.md)."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or os.environ.get("EDGE_LOCAL_STORE_PATH", _DEFAULT_DB_PATH)
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        # check_same_thread=False: this store is accessed from both sync
        # capability code and async governance/negotiation code that may
        # run on different threads of the same event loop's executor;
        # the RLock above serializes all real access, so sqlite3's own
        # thread-affinity check would only get in the way, not add safety.
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (namespace, key)
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_entries_namespace ON cache_entries(namespace)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                    stream TEXT PRIMARY KEY,
                    last_epoch INTEGER NOT NULL DEFAULT 0,
                    last_synced_at REAL NOT NULL DEFAULT 0,
                    cursor TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.commit()

    def put(self, namespace: str, key: str, value: dict[str, Any], provenance: CacheProvenance) -> None:
        """Upsert. A fresh write always supersedes whatever was cached
        before for this (namespace, key) -- this is a lookup cache, not
        an append-only audit trail (durable append-only history already
        exists centrally: AuditLog, timeline stores)."""
        now = time.time()
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO cache_entries (namespace, key, value, provenance, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(namespace, key) DO UPDATE SET
                        value=excluded.value, provenance=excluded.provenance, updated_at=excluded.updated_at
                    """,
                    (namespace, key, json.dumps(value), json.dumps(provenance_to_dict(provenance)), now),
                )
                self._conn.commit()
        except sqlite3.DatabaseError as exc:
            raise EdgeLocalStoreError(f"put failed for {namespace}/{key}: {exc}") from exc

    def get(self, namespace: str, key: str) -> CacheEntry | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT namespace, key, value, provenance, updated_at FROM cache_entries WHERE namespace = ? AND key = ?",
                    (namespace, key),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise EdgeLocalStoreError(f"get failed for {namespace}/{key}: {exc}") from exc
        if row is None:
            return None
        return self._row_to_entry(row)

    def list(self, namespace: str) -> list[CacheEntry]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT namespace, key, value, provenance, updated_at FROM cache_entries WHERE namespace = ?",
                    (namespace,),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise EdgeLocalStoreError(f"list failed for {namespace}: {exc}") from exc
        return [self._row_to_entry(row) for row in rows]

    def get_many(self, namespace: str, keys: list[str]) -> dict[str, CacheEntry]:
        """Batched read (Section 12): one query instead of len(keys)
        round trips through the lock/connection. Returns only the keys
        that were found -- missing keys are simply absent, not None
        entries, so callers can do `result.get(k)` uniformly with `get`."""
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT namespace, key, value, provenance, updated_at FROM cache_entries "
                    f"WHERE namespace = ? AND key IN ({placeholders})",
                    (namespace, *keys),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise EdgeLocalStoreError(f"get_many failed for {namespace}: {exc}") from exc
        return {row["key"]: self._row_to_entry(row) for row in rows}

    def put_many(self, namespace: str, entries: dict[str, tuple[dict[str, Any], CacheProvenance]]) -> None:
        """Batched write (Section 12): one transaction/commit for the
        whole tick's local-cache writes instead of one commit per key.
        This ONLY reduces local storage I/O -- it has no bearing on
        governance, which is still evaluated and recorded per capability
        exactly as before; nothing here batches a security decision."""
        if not entries:
            return
        now = time.time()
        try:
            with self._lock:
                self._conn.executemany(
                    """
                    INSERT INTO cache_entries (namespace, key, value, provenance, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(namespace, key) DO UPDATE SET
                        value=excluded.value, provenance=excluded.provenance, updated_at=excluded.updated_at
                    """,
                    [
                        (namespace, key, json.dumps(value), json.dumps(provenance_to_dict(provenance)), now)
                        for key, (value, provenance) in entries.items()
                    ],
                )
                self._conn.commit()
        except sqlite3.DatabaseError as exc:
            raise EdgeLocalStoreError(f"put_many failed for {namespace}: {exc}") from exc

    def delete(self, namespace: str, key: str) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM cache_entries WHERE namespace = ? AND key = ?", (namespace, key),
                )
                self._conn.commit()
        except sqlite3.DatabaseError as exc:
            raise EdgeLocalStoreError(f"delete failed for {namespace}/{key}: {exc}") from exc

    def clear_namespace(self, namespace: str) -> int:
        """Used by sync reconciliation (Section 7) to replace a whole
        snapshot atomically rather than leaving orphaned entries from a
        previous epoch. Returns the number of rows removed."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM cache_entries WHERE namespace = ?", (namespace,))
            self._conn.commit()
            return cur.rowcount

    def _row_to_entry(self, row: sqlite3.Row) -> CacheEntry:
        try:
            value = json.loads(row["value"])
            provenance = provenance_from_dict(json.loads(row["provenance"]))
        except (json.JSONDecodeError, TypeError) as exc:
            # Corrupt row: fail closed for THIS entry (never return
            # half-parsed data as if it were valid), but do not take
            # down the whole store for one bad record.
            logger.warning(
                "EdgeLocalStore: corrupt cache entry %s/%s, treating as absent: %s",
                row["namespace"], row["key"], exc,
            )
            raise EdgeLocalStoreError(f"corrupt entry {row['namespace']}/{row['key']}") from exc
        return CacheEntry(
            namespace=row["namespace"], key=row["key"], value=value,
            provenance=provenance, updated_at=row["updated_at"],
        )

    # ── Sync bookkeeping (Section 7) ────────────────────────────────

    def get_sync_state(self, stream: str) -> tuple[int, float, str]:
        """Returns (last_epoch, last_synced_at, cursor) for a given sync
        stream (e.g. "policy", "world_projection"). Defaults to (0, 0, "")
        for a stream never synced before -- a fresh device correctly
        requests a full initial snapshot rather than an incremental one."""
        with self._lock:
            row = self._conn.execute(
                "SELECT last_epoch, last_synced_at, cursor FROM sync_state WHERE stream = ?", (stream,),
            ).fetchone()
        if row is None:
            return 0, 0.0, ""
        return int(row["last_epoch"]), float(row["last_synced_at"]), str(row["cursor"])

    def set_sync_state(self, stream: str, *, last_epoch: int, cursor: str = "") -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sync_state (stream, last_epoch, last_synced_at, cursor)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(stream) DO UPDATE SET
                    last_epoch=excluded.last_epoch, last_synced_at=excluded.last_synced_at, cursor=excluded.cursor
                """,
                (stream, last_epoch, time.time(), cursor),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_default_store: EdgeLocalStore | None = None
_default_store_lock = threading.Lock()


def get_edge_local_store() -> EdgeLocalStore:
    global _default_store
    if _default_store is None:
        with _default_store_lock:
            if _default_store is None:
                _default_store = EdgeLocalStore()
    return _default_store


def reset_edge_local_store_for_tests(db_path: str | None = None) -> EdgeLocalStore:
    """Tests should call this with a tmp_path-backed path -- never point
    the real ~/.monkeybrain/edge/local_store.db at test data."""
    global _default_store
    if _default_store is not None:
        _default_store.close()
    _default_store = EdgeLocalStore(db_path) if db_path else None
    return _default_store if _default_store is not None else get_edge_local_store()
