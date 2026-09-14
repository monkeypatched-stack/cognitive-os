"""SecureKeystore — user-scoped API key storage with Fernet encryption.

Keys are encrypted at rest using a Fernet symmetric key derived from the
required KEYSTORE_SECRET env var. Persisted to Redis (HSET, one field per
key_id — the same incremental, O(1)-per-mutation pattern established for
KnowledgeGraph/actors/context this session — REDIS_URL with a
REDIS_HOST/REDIS_PORT fallback, matching TimelineStore/RunStore/
IdempotencyStore) so stored keys survive a restart; falls back to
in-memory-only if Redis is unreachable, same as every other store in this
codebase. All operations are scoped to the requesting user_id.

Gate 7 (Security) finding: this module previously minted a silent,
per-boot ephemeral Fernet key whenever KEYSTORE_SECRET was unset — every
stored key became permanently undecryptable on the next restart, with
only a WARNING log (easy to miss) rather than a hard failure.
routes/keys.py's own docstring already claimed "it now refuses to start
without one," but the code never actually did that — this file now
matches that claim: no KEYSTORE_SECRET means SecureKeystore() raises,
not degrades silently. Persisting Fernet-encrypted blobs under a key that
changes every boot would have been actively worse than not persisting
them (confidently wrong data instead of honest data loss), so the
fail-fast fix had to land before persistence could safely be added.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, UTC
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.keystore")

_SENTINEL = object()
_KEYSTORE_HASH_KEY = "monkeybrain:keystore:keys"


def _get_cipher():
    from cryptography.fernet import Fernet

    secret = os.getenv("KEYSTORE_SECRET")
    if not secret:
        raise RuntimeError(
            "KEYSTORE_SECRET is not set. Refusing to start with a silently-generated "
            "ephemeral key — every stored API key would become permanently "
            "undecryptable on the next restart. Set KEYSTORE_SECRET to a stable, "
            "real Fernet key (cryptography.fernet.Fernet.generate_key()) before "
            "using this keystore."
        )
    key = secret.encode() if isinstance(secret, str) else secret
    return Fernet(key)


def _make_redis_client() -> Any:
    """Same REDIS_URL-with-REDIS_HOST/PORT-fallback pattern already
    established for TimelineStore/RunStore/IdempotencyStore this session
    — a keystore that silently doesn't persist is exactly the class of
    gap those fixes closed elsewhere."""
    try:
        import redis as _redis

        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            url = f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"
        client = _redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        return client
    except Exception as exc:
        logger.warning("SecureKeystore: Redis unavailable, falling back to in-memory only: %s", exc)
        return None


class SecureKeystore:
    """Redis-persisted (when reachable) keystore with Fernet-encrypted
    API key values."""

    def __init__(self, redis_client: Any = _SENTINEL) -> None:
        self._cipher = _get_cipher()
        self._store: dict[str, dict[str, Any]] = {}  # key_id → record
        self._redis = _make_redis_client() if redis_client is _SENTINEL else redis_client
        self._load()

    def _load(self) -> None:
        if self._redis is None:
            return
        try:
            raw = self._redis.hgetall(_KEYSTORE_HASH_KEY)
            for key_id, value in raw.items():
                self._store[key_id] = json.loads(value)
            if raw:
                logger.info("SecureKeystore loaded: %d keys", len(raw))
        except Exception as exc:
            logger.warning("SecureKeystore load failed: %s", exc)

    def _persist(self, key_id: str, record: dict[str, Any]) -> None:
        if self._redis is None:
            return
        try:
            self._redis.hset(_KEYSTORE_HASH_KEY, key_id, json.dumps(record))
        except Exception as exc:
            logger.warning("SecureKeystore persist failed for %r: %s", key_id, exc)

    def _delete_persisted(self, key_id: str) -> None:
        if self._redis is None:
            return
        try:
            self._redis.hdel(_KEYSTORE_HASH_KEY, key_id)
        except Exception as exc:
            logger.warning("SecureKeystore delete failed for %r: %s", key_id, exc)

    def add_key(
        self,
        user_id: str,
        service: str,
        key_name: str,
        api_key: str,
        api_url: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key_id = str(uuid4())
        encrypted = self._cipher.encrypt(api_key.encode()).decode()
        record: dict[str, Any] = {
            "key_id": key_id,
            "user_id": user_id,
            "service": service,
            "key_name": key_name,
            "api_url": api_url,
            "config": config or {},
            "_encrypted": encrypted,
            "created_at": datetime.now(UTC).isoformat(),
            "is_active": True,
        }
        self._store[key_id] = record
        self._persist(key_id, record)
        return self._safe_record(record)

    def list_keys(self, user_id: str, service: str | None = None) -> list[dict[str, Any]]:
        results = []
        for record in self._store.values():
            if record["user_id"] != user_id:
                continue
            if service and record["service"] != service:
                continue
            results.append(self._safe_record(record))
        return results

    def get_key(self, key_id: str, user_id: str) -> dict[str, Any] | None:
        record = self._store.get(key_id)
        if record is None or record["user_id"] != user_id:
            return None
        return self._safe_record(record)

    def get_plaintext(self, key_id: str, user_id: str) -> str | None:
        record = self._store.get(key_id)
        if record is None or record["user_id"] != user_id:
            return None
        return self._cipher.decrypt(record["_encrypted"].encode()).decode()

    def remove_key(self, key_id: str, user_id: str) -> bool:
        record = self._store.get(key_id)
        if record is None or record["user_id"] != user_id:
            return False
        del self._store[key_id]
        self._delete_persisted(key_id)
        return True

    def get_config(self, service: str, user_id: str) -> dict[str, Any] | None:
        for record in self._store.values():
            if record["user_id"] == user_id and record["service"] == service and record["is_active"]:
                return {
                    "service": service,
                    "api_url": record["api_url"],
                    **record["config"],
                }
        return None

    def summary(self, user_id: str | None = None) -> dict[str, Any]:
        records = [r for r in self._store.values() if user_id is None or r["user_id"] == user_id]
        services: dict[str, int] = {}
        for r in records:
            services[r["service"]] = services.get(r["service"], 0) + 1
        return {
            "total_keys": len(records),
            "active_keys": sum(1 for r in records if r["is_active"]),
            "services": services,
        }

    @staticmethod
    def _safe_record(record: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in record.items() if k != "_encrypted"}
