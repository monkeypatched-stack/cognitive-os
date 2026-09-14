"""LoginInfo persistence — Redis-backed, same pattern established this
session for TimelineStore/RunStore/IdempotencyStore/SecureKeystore
(REDIS_URL with a REDIS_HOST/REDIS_PORT fallback; HSET/HGET per actor_id,
O(1) per mutation). Falls back to in-memory-only if Redis is unreachable.

Exists because routes/actor_profile.py's login/logout/sessions endpoints
were found to be entirely fake — login() accepted ANY email/password and
returned a hardcoded "mock_token_"+actor_id string, logout() and
sessions() always returned canned/empty responses regardless of reality.
kernel/login_info.py already had a real, complete implementation
(PBKDF2-HMAC-SHA256 password hashing, constant-time verification,
account lockout after repeated failures, session tracking) that nothing
in the REST layer ever called. This module is the missing persistence
layer that lets that real implementation actually be used statefully
across requests.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from src.monkey_brain.kernel.login_info import LoginInfo

logger = logging.getLogger("agentos.login_store")

_LOGIN_HASH_KEY = "monkeybrain:login_info"
_SENTINEL = object()


def _make_redis_client() -> Any:
    try:
        import redis as _redis

        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            url = f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"
        client = _redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        return client
    except Exception as exc:
        logger.warning("LoginStore: Redis unavailable, falling back to in-memory only: %s", exc)
        return None


class LoginStore:
    """actor_id -> LoginInfo. Singleton, matching every other store this
    session established (RunStore/IdempotencyStore/SecureKeystore)."""

    _instance: "LoginStore | None" = None

    def __new__(cls) -> "LoginStore":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._redis = _make_redis_client()
            instance._cache: dict[str, LoginInfo] = {}
            cls._instance = instance
        return cls._instance

    def get(self, actor_id: str) -> LoginInfo | None:
        if actor_id in self._cache:
            return self._cache[actor_id]
        if self._redis is not None:
            try:
                import json

                raw = self._redis.hget(_LOGIN_HASH_KEY, actor_id)
                if raw:
                    info = LoginInfo.from_dict(json.loads(raw))
                    self._cache[actor_id] = info
                    return info
            except Exception as exc:
                logger.warning("LoginStore load failed for %r: %s", actor_id, exc)
        return None

    def save(self, actor_id: str, info: LoginInfo) -> None:
        self._cache[actor_id] = info
        if self._redis is not None:
            try:
                import json

                self._redis.hset(_LOGIN_HASH_KEY, actor_id, json.dumps(info.to_dict()))
            except Exception as exc:
                logger.warning("LoginStore save failed for %r: %s", actor_id, exc)


def get_login_store() -> LoginStore:
    return LoginStore()
