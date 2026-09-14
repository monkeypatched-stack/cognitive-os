"""World Backup — Gate 6 (Persistence): backup / restore / migration.

Everything PlanetaryRuntime persists (ADR-011's actors/context fix, this
session's KnowledgeGraph fix, plus the pre-existing world/geography/
societies/relationships saves) lives under `monkeybrain:*` Redis keys of
three types: STRING (one JSON blob — world, geography, societies,
relationships), HASH (one field per object — actors, knowledge_graph
entities/relationships), and LIST (append-only — context events).
export_backup() reads all of them generically by key pattern rather than
hardcoding each subsystem's key name a second time (this file would
silently go stale the next time someone adds a `monkeybrain:whatever`
key otherwise) and bundles them into one portable JSON document with a
schema_version tag — the "migration" support this Gate asks for: a future
format change bumps this version and the restore path below (or a
dedicated migration function keyed off it) handles the old shape, the
same forward-compatible-legacy-fallback pattern ADR-011 already
established for the actors/context Redis formats individually, just
applied once, at the whole-backup level, instead of per-key.

Restore writes the backed-up keys back to Redis and returns a report —
it does NOT re-run PlanetaryRuntime's `_load_*()` methods against a live,
already-booted instance (those only run once, in `__init__`; re-running
them against in-memory state that has since diverged risks silently
resurrecting an entity a live actor already deleted, or duplicating
relationships). A restore takes full effect on the next process restart,
matching how restore-from-backup works in most real systems. Documented
here rather than pretending a live, in-place restore is safe when it
genuinely is not proven to be.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("agentos.world_backup")

SCHEMA_VERSION = 1
_KEY_PREFIX = "monkeybrain:"


def export_backup(planetary_runtime: Any) -> dict[str, Any]:
    """Read every monkeybrain:* Redis key PlanetaryRuntime persists into
    one portable document. Read-only — never mutates anything."""
    redis = getattr(planetary_runtime, "_redis", None)
    if redis is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "created_at": time.time(),
            "available": False,
            "reason": "no Redis connection — this PlanetaryRuntime has nothing persisted to back up",
            "keys": {},
        }

    keys: dict[str, dict[str, Any]] = {}
    for key in redis.keys(f"{_KEY_PREFIX}*"):
        try:
            key_type = redis.type(key)
            if key_type == "string":
                keys[key] = {"type": "string", "value": redis.get(key)}
            elif key_type == "hash":
                keys[key] = {"type": "hash", "value": redis.hgetall(key)}
            elif key_type == "list":
                keys[key] = {"type": "list", "value": redis.lrange(key, 0, -1)}
            else:
                logger.debug(
                    "world_backup: skipping key %r of unsupported type %r",
                    key,
                    key_type,
                )
        except Exception as exc:
            logger.warning("world_backup: failed to read key %r: %s", key, exc)

    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": time.time(),
        "available": True,
        "key_count": len(keys),
        "keys": keys,
    }


def restore_backup(planetary_runtime: Any, backup: dict[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
    """Write a previously-exported backup's keys back to Redis.

    overwrite=False (default) refuses to touch a key that already exists
    — a restore is meant to repopulate an empty/fresh environment (a new
    deployment, a disaster-recovery target), not silently clobber a live
    one that already has its own real, current data. Pass overwrite=True
    deliberately (e.g. an explicit "restore to this known-good backup,
    discard current state" operation) to allow it.

    Takes full effect on the NEXT process restart — see module docstring
    for why this does not attempt a live, in-place reload.
    """
    redis = getattr(planetary_runtime, "_redis", None)
    if redis is None:
        return {
            "restored": False,
            "reason": "no Redis connection on this PlanetaryRuntime",
        }

    schema_version = backup.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        return {
            "restored": False,
            "reason": f"unsupported schema_version {schema_version!r} (this build supports {SCHEMA_VERSION})",
        }

    restored = 0
    skipped_existing = 0
    failed = 0
    for key, entry in backup.get("keys", {}).items():
        try:
            if not overwrite and redis.exists(key):
                skipped_existing += 1
                continue
            key_type = entry.get("type")
            value = entry.get("value")
            if key_type == "string":
                redis.set(key, value)
            elif key_type == "hash":
                redis.delete(key)
                if value:
                    redis.hset(key, mapping=value)
            elif key_type == "list":
                redis.delete(key)
                if value:
                    redis.rpush(key, *value)
            else:
                failed += 1
                continue
            restored += 1
        except Exception as exc:
            logger.warning("world_backup: failed to restore key %r: %s", key, exc)
            failed += 1

    return {
        "restored": True,
        "keys_written": restored,
        "keys_skipped_existing": skipped_existing,
        "keys_failed": failed,
        "note": "takes full effect on next process restart — see world_backup.py module docstring",
    }
